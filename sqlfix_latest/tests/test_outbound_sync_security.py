import asyncio
from app.integrations.prod.net_safety import assert_public_url


def test_private_and_metadata_urls_are_blocked():
    async def run():
        for url in (
            "http://127.0.0.1:8000/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
        ):
            reason = await assert_public_url(url)
            assert reason

    asyncio.run(run())
