from openai import AsyncOpenAI
from utils import _env
from logging import getLogger


client = AsyncOpenAI(
    base_url="https://ai.hackclub.com/proxy/v1", api_key=_env("HCAI_API_KEY")
)


logger = getLogger(__name__)


async def is_negative(text: str) -> bool:
    prompt = "Is the following message negative in sentiment? Answer strictly with 'yes' or 'no'."
    response = await client.chat.completions.create(
        model="google/gemini-3-flash-preview",
        messages=[{"role": "user", "content": f"{prompt}\n\n{text}"}],
        max_tokens=5,
    )
    mcontent = response.choices[0].message.content
    if not mcontent:
        logger.warning(f"No sentiment?: {response}")
        return False
    answer = mcontent.strip().lower()
    return "yes" in answer


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(is_negative(f"Is the following negative? '{input()}'")))
