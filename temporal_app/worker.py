import asyncio
import logging
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from temporal_app.activities.llm_activities import execute_tool, propose_next_action
from temporal_app.workflows.chat import ChatWorkflow
from temporal_app.workflows.music import MusicWorkflow
from temporal_app.activities.music import (
    plan_music_search, post_song_to_slack, recommend_song,
    search_spotify, update_music_preferences,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = "chat-task-queue"


async def run_worker() -> None:
    client = await Client.connect(TEMPORAL_HOST)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ChatWorkflow, MusicWorkflow],
        activities=[propose_next_action, execute_tool, plan_music_search,
                    post_song_to_slack, recommend_song, search_spotify, update_music_preferences],
    )
    logger.info("Worker started — task queue: %s", TASK_QUEUE)
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
