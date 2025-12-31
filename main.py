import asyncio
import aiohttp
import logging
import os
import re

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from db import (
    ensure_schema,
    get_client,
    add_member,
    remove_member,
    list_members,
    list_blammed,
    list_whitelisted,
    get_idv_required_level,
    set_idv_required_level,
    needs_sync,
    marksync,
    list_tracked_channels,
    set_members,
)
from idv import is_idved, is_idved_under18, user_is_bot
from utils import (
    _env,
    _parse_mention,
    _resolve_bot_user_id,
    _kick_xoxc,
    _get_channel_allowed_users,
    _set_channel_allowed_users,
    _fetch_channel_managers,
    _fetch_channel_members,
)

app = AsyncApp(
    token=_env("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),  # intentionally optional
)

BASE_CMD = "/" + _env("SLACK_BASE_CMD").lstrip("/")
ADMIN_ID = None
BOT_USER_ID = None


@app.command(BASE_CMD)
async def handle_blam(ack, respond, command, logger):
    await ack()
    # subcommand tree:
    # idv: off/required/under18 (none: show status)
    # whitelist: add/remove/list
    tokens = command.get("text", "").strip().split()
    if not tokens or tokens[0] == "help":
        await respond(
            f"Usage:\n"
            f"`{BASE_CMD} idv [off|required|under18]`\n"
            f"`{BASE_CMD} whitelist [add|remove|list] [@user]`"
        )
        return

    subcommand = tokens[0]
    match subcommand:
        case "idv":
            if len(tokens) == 1:
                level = await get_idv_required_level(command["channel_id"])
                level_str = {0: "off", 1: "required", 2: "under18"}.get(
                    level, "unknown"
                )
                await respond(f"IDV requirement is currently set to: *{level_str}*")
                return
            if len(tokens) > 2:
                await respond(f"Usage: `{BASE_CMD} idv [off|required|under18]`")
                return
            level = tokens[1]
            levels = {"off": 0, "required": 1, "under18": 2}
            if level not in levels:
                await respond(f"Usage: `{BASE_CMD} idv [off|required|under18]`")
                return
            await set_idv_required_level(command["channel_id"], levels[level])
            await sync_channel(command["channel_id"], logger)
            await respond(f"IDV requirement set to *{level}*.")
        case "whitelist":
            if len(tokens) < 2:
                await respond(
                    f"Usage: `{BASE_CMD} whitelist [add|remove|list] [@user]`"
                )
                return
            action = tokens[1]
            if action == "list":
                whitelisted = await list_whitelisted(command["channel_id"])
                if not whitelisted:
                    await respond("No users are whitelisted.")
                    return
                user_mentions = " ".join(f"<@{uid}>" for uid in whitelisted)
                await respond(f"Whitelisted users: {user_mentions}")
                return
            if len(tokens) != 3:
                await respond(
                    f"Usage: `{BASE_CMD} whitelist [add|remove|list] [@user]`"
                )
                return
            user_mention = tokens[2]
            user_id = _parse_mention(user_mention)
            if not user_id:
                await respond("Please mention a valid user.")
                return
            if action == "add":
                await add_member(command["channel_id"], user_id)
                await respond(f"User <@{user_id}> added to whitelist.")
            elif action == "remove":
                await remove_member(command["channel_id"], user_id)
                await respond(f"User <@{user_id}> removed from whitelist.")
            else:
                await respond(
                    f"Usage: `{BASE_CMD} whitelist [add|remove|list] [@user]`"
                )
        case "user":
            # action: blam, unblam, list
            # tbd, will figure out later
            # weird stuff going on
            await respond("User subcommand is temporarily nonfunctional.")
        case _:
            await respond("Unknown subcommand. Use `help` for usage information.")


async def sync_channel(channel_id: str, logger) -> None:
    logging.info("Syncing channel %s", channel_id)
    if not await needs_sync(channel_id):
        return

    members = await list_members(channel_id)
    blammed = set(await list_blammed(channel_id))
    whitelisted = set(await list_whitelisted(channel_id))
    idv_level = await get_idv_required_level(channel_id)
    managers = await _fetch_channel_managers(channel_id)

    allowed_users = []

    for user_id in members:
        if user_id in whitelisted or user_id == ADMIN_ID or user_id in managers:
            allowed_users.append(user_id)
            continue

        if user_id in blammed:
            continue

        if idv_level == 0:
            allowed_users.append(user_id)
            continue

        is_bot = await user_is_bot(user_id, app.client, logger)
        if is_bot:
            allowed_users.append(user_id)
            continue

        if idv_level == 1 and await is_idved(user_id, logger):
            allowed_users.append(user_id)
        elif idv_level == 2 and await is_idved_under18(user_id, logger):
            allowed_users.append(user_id)

    await _set_channel_allowed_users(channel_id, allowed_users)
    await marksync(channel_id)


@app.event("member_joined_channel")
async def handle_member_joined_channel(body, say, logger):
    event = body.get("event", {})
    user_id = event.get("user")
    channel_id = event.get("channel")

    if not channel_id or not user_id:
        return

    if user_id == body.get("authorizations", [{}])[0].get("user_id"):
        try:
            await AsyncWebClient(token=_env("SLACK_BOT_TOKEN")).conversations_invite(
                channel=channel_id, users=str(ADMIN_ID)
            )
        except SlackApiError as exc:
            logger.warning("Failed to invite admin to channel", exc_info=exc)

    await add_member(channel_id, user_id)
    await sync_channel(channel_id, logger)


@app.event("member_left_channel")
async def handle_member_left_channel(body, logger):
    event = body.get("event", {})
    channel_id = event.get("channel")
    user_id = event.get("user")

    if not channel_id or not user_id:
        return

    if BOT_USER_ID and user_id == BOT_USER_ID:
        await _invite_bot(channel_id, logger)
        return

    if user_id == ADMIN_ID:
        await _invite_user(channel_id, str(ADMIN_ID), logger)
        return

    await remove_member(channel_id, user_id)
    await sync_channel(channel_id, logger)


async def _invite_user(
    channel_id: str, user_id: str, logger, *, token: str | None = None
):
    try:
        token_to_use = token or _env("SLACK_BOT_TOKEN")
        client = AsyncWebClient(token=token_to_use)
        await client.conversations_invite(channel=channel_id, users=str(user_id))
    except SlackApiError as exc:
        if exc.response.get("error") == "already_in_channel":
            return
        logger.warning("Invite failed", exc_info=exc)


async def _invite_bot(channel_id: str, logger) -> None:
    admin_token = _env("SLACK_PERSONAL_TOKEN")
    await _invite_user(channel_id, str(BOT_USER_ID), logger, token=admin_token)


async def init_channels(logger) -> None:
    channels = await list_tracked_channels()
    logger.info(f"Initializing {len(channels)} tracked channels")
    for channel_id in channels:
        try:
            members = await _fetch_channel_members(channel_id)
            await set_members(channel_id, members)
            await sync_channel(channel_id, logger)
        except Exception as exc:
            logger.warning(f"Failed to init channel {channel_id}", exc_info=exc)


async def main() -> None:
    global ADMIN_ID
    global BOT_USER_ID
    ADMIN_ID = str(_env("ADMIN_ID"))
    BOT_USER_ID = str(await _resolve_bot_user_id(app))
    await ensure_schema()
    await init_channels(logging.getLogger(__name__))

    handler = AsyncSocketModeHandler(app, _env("SLACK_APP_TOKEN"))
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
