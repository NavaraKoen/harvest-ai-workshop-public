import asyncio
import hashlib
import hmac
import json
import os
import shutil
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from api.main import app
from temporal_app.activities.music import recommend_song, update_music_preferences
from temporal_app.workflows.music import MusicWorkflow


class RecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_invented_track(self):
        llm = AsyncMock()
        llm.chat.return_value = '{"track_id":"invented","reason":"Great fit"}'
        with patch("temporal_app.activities.music.create_llm", return_value=llm):
            with self.assertRaises(ValueError):
                await recommend_song("More guitars", [{"id": "real"}])

    async def test_negative_feedback_is_amplified_in_prompt(self):
        llm = AsyncMock()
        llm.chat.return_value = '{"preferences":"Prioritize very noisy metal"}'
        with patch("temporal_app.activities.music.create_llm", return_value=llm):
            result = await update_music_preferences("Explore rock", [
                {"text": "I absolutely hate noisy metal", "song": {"name": "Metal song"}},
            ])
        self.assertEqual(result["preferences"], "Prioritize very noisy metal")
        messages = llm.chat.call_args.args[0]
        self.assertIn("Scale the strength with the negativity", messages[0].content)
        self.assertIn("do not turn them into exclusions", messages[0].content)
        self.assertIn("Metal song", messages[1].content)

    async def test_signed_slack_feedback_and_replay_protection(self):
        payload = {"event_id": "Ev1", "event": {
            "type": "message", "channel": "C1", "user": "U1",
            "thread_ts": "123.45", "text": "Too much guitar",
        }}
        raw = json.dumps(payload).encode()
        client = AsyncMock()
        handle = AsyncMock()
        client.get_workflow_handle = lambda _: handle

        def headers(timestamp):
            signature = "v0=" + hmac.new(b"secret", b"v0:" + timestamp.encode() + b":" + raw,
                                         hashlib.sha256).hexdigest()
            return {"x-slack-request-timestamp": timestamp, "x-slack-signature": signature,
                    "content-type": "application/json"}

        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "secret", "SLACK_CHANNEL_ID": "C1"}), \
                patch("api.main._client", return_value=client):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                response = await http.post("/api/slack/events", content=raw, headers=headers(str(int(time.time()))))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(handle.signal.call_args.args[1]["text"], "Too much guitar")
                response = await http.post("/api/slack/events", content=raw, headers=headers("1"))
                self.assertEqual(response.status_code, 401)
                response = await http.post("/api/slack/events", content=raw)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(handle.signal.await_count, 1)


@unittest.skipUnless(shutil.which("temporal"), "Temporal CLI required")
class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_minute_loop_feedback_deduplication_stop_and_replay(self):
        posted = []
        profiles = []
        feedback_batches = []
        log = {"input": [], "raw_output": "{}"}

        @activity.defn(name="plan_music_search")
        async def plan(preferences: str, recent: list[dict]) -> dict:
            profiles.append(preferences)
            return {"query": "rock", "log": log}

        @activity.defn(name="search_spotify")
        async def search(query: str) -> list[dict]:
            return [{"id": str(n), "name": f"Song {n}", "artists": "Artist", "url": "https://open.spotify.com/track/test"}
                    for n in range(3)]

        @activity.defn(name="recommend_song")
        async def recommend(preferences: str, tracks: list[dict]) -> dict:
            return {"song": {**tracks[0], "reason": "More guitars"}, "log": log}

        @activity.defn(name="post_song_to_slack")
        async def post(channel: str, song: dict, message_id: str) -> str:
            posted.append(song)
            return str(len(posted))

        @activity.defn(name="update_music_preferences")
        async def update(preferences: str, feedback: list[dict]) -> dict:
            feedback_batches.append(feedback)
            return {"preferences": "Even more guitars", "log": log}

        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=shutil.which("temporal"),
        ) as env:
            async with Worker(env.client, task_queue="music-test", workflows=[MusicWorkflow],
                              activities=[plan, search, recommend, post, update]):
                handle = await env.client.start_workflow(MusicWorkflow.run, args=["C1", "Guitars", True],
                                                         id="music-test", task_queue="music-test")

                async def wait_for_round(count):
                    async with asyncio.timeout(75):
                        while len(posted) < count or (await handle.query(MusicWorkflow.get_state))["state"] != "waiting_interval":
                            await asyncio.sleep(0.1)

                await wait_for_round(1)
                event = {"event_id": "Ev1", "thread_ts": "1", "text": "I hate guitars"}
                await handle.signal(MusicWorkflow.slack_feedback, event)
                await handle.signal(MusicWorkflow.slack_feedback, event)
                await handle.signal(MusicWorkflow.slack_feedback, {**event, "event_id": "Ev2", "thread_ts": "unrelated"})
                await handle.signal(MusicWorkflow.send_user_input, "Way too much guitar")
                await asyncio.sleep(0.3)
                self.assertEqual(len(posted), 1, "Feedback must not trigger an early recommendation")
                await wait_for_round(2)
                self.assertEqual(len(feedback_batches[0]), 2)
                self.assertEqual(feedback_batches[0][0]["song"]["id"], "0")
                self.assertEqual(profiles, ["Guitars", "Even more guitars"])
                self.assertEqual([song["id"] for song in posted], ["0", "1"])
                await handle.signal(MusicWorkflow.stop)
                self.assertEqual(await handle.result(), "Music recommendations stopped")
                history = await handle.fetch_history()
                timer_events = [e for e in history.events if e.HasField("timer_started_event_attributes")]
                self.assertTrue(timer_events)
                self.assertGreater(timer_events[0].timer_started_event_attributes.start_to_fire_timeout.seconds, 50)
                await Replayer(workflows=[MusicWorkflow]).replay_workflow(history)
