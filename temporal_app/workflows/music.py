from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities.music import (
        plan_music_search, post_song_to_slack, recommend_song,
        search_spotify, update_music_preferences,
    )


@workflow.defn
class MusicWorkflow:
    def __init__(self) -> None:
        self.preferences = "Discover a variety of songs; learn my taste from feedback."
        self.history: list[dict] = []
        self.logs: list[dict] = []
        self.feedback: list[dict] = []
        self.recent: list[dict] = []
        self.seen_events: list[str] = []
        self.state = "starting"
        self.stopped = False
        self.call_count = 0

    @workflow.signal
    def send_user_input(self, message: str) -> None:
        if message.strip():
            self.feedback.append({"text": message[:4000],
                                  "song": self.recent[-1] if self.recent else None})
            self.history.append({"role": "user", "content": message[:4000]})

    @workflow.signal
    def slack_feedback(self, event: dict) -> None:
        song = next((s for s in self.recent if s.get("thread_ts") == event["thread_ts"]), None)
        if song is None or event["event_id"] in self.seen_events:
            return
        self.seen_events = (self.seen_events + [event["event_id"]])[-500:]
        self.feedback.append({"text": event["text"][:4000], "song": song})
        self.history.append({"role": "user", "content": f"Slack feedback: {event['text'][:4000]}"})

    @workflow.signal
    def stop(self) -> None:
        self.stopped = True

    @workflow.query
    def get_history(self) -> list[dict]:
        return self.history

    @workflow.query
    def get_llm_log(self) -> list[dict]:
        return self.logs

    @workflow.query
    def get_state(self) -> dict:
        return {"state": self.state, "preferences": self.preferences}

    def log(self, result: dict, action: str) -> None:
        self.call_count += 1
        self.logs.append({**result["log"], "call": self.call_count, "action_type": action})
        self.logs = self.logs[-30:]

    @workflow.run
    async def run(self, channel: str, initial_preferences: str = "", no_retries: bool = False,
                  saved: dict | None = None) -> str:
        if saved:
            for key, value in saved.items():
                setattr(self, key, value)
        elif initial_preferences:
            self.preferences = initial_preferences
        retry = RetryPolicy(maximum_attempts=1 if no_retries else 3)

        async def execute(fn, *args):
            return await workflow.execute_activity(
                fn, args=args, start_to_close_timeout=timedelta(seconds=120), retry_policy=retry,
            )

        for _ in range(100):
            if self.stopped:
                break
            started = workflow.now()
            self.state = "running"
            # Remove only the batch being processed; signals arriving during an activity
            # remain queued for the following iteration.
            if self.feedback:
                batch, self.feedback = self.feedback, []
                result = await execute(update_music_preferences, self.preferences, batch)
                self.preferences = result["preferences"]
                self.log(result, "update_preferences")
            plan = await execute(plan_music_search, self.preferences, self.recent)
            self.log(plan, "search_spotify")
            tracks = await execute(search_spotify, plan["query"])
            previous = {song["id"] for song in self.recent}
            candidates = [track for track in tracks if track["id"] not in previous]
            if candidates and not self.stopped:
                result = await execute(recommend_song, self.preferences, candidates)
                self.log(result, "recommend_song")
                song = result["song"]
                if not self.stopped:
                    ts = await execute(post_song_to_slack, channel, song, str(workflow.uuid4()))
                    self.recent = (self.recent + [{**song, "thread_ts": ts}])[-100:]
                    self.history.append({"role": "assistant", "content":
                        f"{song['name']} — {song['artists']}\n{song['url']}\n{song['reason']}\nSent to Slack."})
            elif not self.stopped:
                self.history.append({"role": "system", "content":
                    "No new Spotify candidates this round. Trying another search next minute."})
            self.history = self.history[-200:]
            self.state = "waiting_interval"
            # A durable timer, anchored to the start of this cycle. Slow cycles never overlap.
            remaining = max(0, 60 - (workflow.now() - started).total_seconds())
            if not self.stopped and remaining:
                try:
                    await workflow.wait_condition(lambda: self.stopped, timeout=remaining)
                except TimeoutError:
                    pass
        if self.stopped:
            self.state = "ended"
            return "Music recommendations stopped"
        workflow.continue_as_new(args=[channel, initial_preferences, no_retries, {
            key: getattr(self, key) for key in (
                "preferences", "history", "logs", "feedback", "recent", "seen_events", "call_count",
            )
        }])
