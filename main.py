import asyncio
import aiohttp
import logging
import os
import re
import time

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from db import (
    clear_user_slowmoded,
    ensure_schema,
    get_client,
    add_member,
    remove_member,
    list_members,
    list_blammed,
    list_whitelisted,
    get_idv_required_level,
    set_channel_slowmode_time,
    get_channel_slowmode_time,
    set_user_slowmoded,
    get_user_slowmoded,
    set_idv_required_level,
    needs_sync,
    list_tracked_channels,
    set_members,
    set_managers,
    list_managers,
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
async def handle_blam(ack, respond, command):
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
            await sync_channel(command["channel_id"])
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
        case "slowmode":
            if len(tokens) == 1:
                await respond(f"Usage: `{BASE_CMD} slowmode [off|<seconds>]`")
                return
            setting = tokens[1]
            if setting == "off":
                seconds = 0
            else:
                if not setting.isdigit() or int(setting) < 0:
                    await respond(f"Usage: `{BASE_CMD} slowmode [off|<seconds>]`")
                    return
                seconds = int(setting)
            if seconds != 0 and not (5 <= seconds <= 60):
                await respond("Slowmode must be between 5 and 60 seconds, or 'off'.")
                return
            await set_channel_slowmode_time(command["channel_id"], seconds)
            await respond(
                f"Slowmode set to {'off' if seconds == 0 else f'{seconds} seconds'}."
            )

        case "user":
            # action: blam, unblam, list
            # tbd, will figure out later
            # weird stuff going on
            await respond("User subcommand is temporarily nonfunctional.")
        case _:
            await respond("Unknown subcommand. Use `help` for usage information.")


async def _process_user_permissions(
    channel_id: str,
    user_id: str,
    blammed: set,
    whitelisted: set,
    idv_level: int,
    managers: set,
) -> bool:
    if user_id == ADMIN_ID or user_id in managers:
        return True

    slowmoded_until = await get_user_slowmoded(channel_id, user_id)
    if slowmoded_until is not None:
        now = time.time()
        expires_timestamp = float(slowmoded_until)
        if expires_timestamp > now:

            return False
        await clear_user_slowmoded(channel_id, user_id)

    if user_id in blammed:
        return False

    if user_id in whitelisted:
        return True

    if idv_level == 0:
        return True

    is_bot = await user_is_bot(user_id, app.client)
    if is_bot:
        return True

    if idv_level == 1:
        return await is_idved(user_id)
    elif idv_level == 2:
        return await is_idved_under18(user_id)

    return False


async def sync_channel(channel_id: str) -> None:
    logger.info("sync: %s", channel_id)
    if not await needs_sync(channel_id):
        return

    members = await list_members(channel_id)
    blammed = set(await list_blammed(channel_id))
    whitelisted = set(await list_whitelisted(channel_id))
    idv_level = await get_idv_required_level(channel_id)
    managers = set(await _fetch_channel_managers(channel_id))
    await set_managers(channel_id, list(managers))

    tasks = [
        _process_user_permissions(
            channel_id, user_id, blammed, whitelisted, idv_level, managers
        )
        for user_id in members
    ]
    results = await asyncio.gather(*tasks)

    allowed_users = [user_id for user_id, allowed in zip(members, results) if allowed]

    await _set_channel_allowed_users(channel_id, allowed_users)


@app.event("message")
async def handle_message_events(body):
    event = body.get("event", {})
    channel_id = event.get("channel")
    user_id = event.get("user")
    if not channel_id or not user_id:
        return

    if user_id in (ADMIN_ID, BOT_USER_ID):
        return

    slowmode_time = await get_channel_slowmode_time(channel_id)
    if not slowmode_time:
        return

    managers = set(await list_managers(channel_id))
    if user_id in managers:
        return

    now = time.time()
    existing_slowmode = await get_user_slowmoded(channel_id, user_id)

    if existing_slowmode is not None:
        existing_expires = float(existing_slowmode)
        if existing_expires > now:
            penalty_time = slowmode_time * 2
            new_expires = existing_expires + penalty_time
            await set_user_slowmoded(channel_id, user_id, str(new_expires))

            remaining = int(new_expires - now)
            try:
                await app.client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"You're still in slowmode! Your slowmode has been extended by {penalty_time} seconds. You can post again in {remaining} seconds.",
                )
            except Exception as e:
                logger.error(e)
            return

    expires_at = now + slowmode_time
    await set_user_slowmoded(channel_id, user_id, str(expires_at))

    allowed_users = await _get_channel_allowed_users(channel_id)
    if user_id not in allowed_users:
        return

    allowed_users.remove(user_id)
    await _set_channel_allowed_users(channel_id, allowed_users)

    async def scheduled_sync():
        await asyncio.sleep(slowmode_time)
        await sync_channel(channel_id)

    asyncio.create_task(scheduled_sync())


@app.event("member_joined_channel")
async def handle_member_joined_channel(body, say):
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
    await sync_channel(channel_id)


@app.event("member_left_channel")
async def handle_member_left_channel(body):
    event = body.get("event", {})
    channel_id = event.get("channel")
    user_id = event.get("user")

    if not channel_id or not user_id:
        return

    if BOT_USER_ID and user_id == BOT_USER_ID:
        await _invite_bot(channel_id)
        return

    if user_id == ADMIN_ID:
        await _invite_user(channel_id, str(ADMIN_ID))
        return

    await remove_member(channel_id, user_id)
    await sync_channel(channel_id)


async def _invite_user(channel_id: str, user_id: str, *, token: str | None = None):
    try:
        token_to_use = token or _env("SLACK_BOT_TOKEN")
        client = AsyncWebClient(token=token_to_use)
        await client.conversations_invite(channel=channel_id, users=str(user_id))
    except SlackApiError as exc:
        if exc.response.get("error") == "already_in_channel":
            return
        logger.warning("Invite failed", exc_info=exc)


async def _invite_bot(channel_id: str) -> None:
    admin_token = _env("SLACK_PERSONAL_TOKEN")
    await _invite_user(channel_id, str(BOT_USER_ID), token=admin_token)


async def init_channels() -> None:
    channels = await list_tracked_channels()
    logger.info(f"Initializing {len(channels)} tracked channels")
    for channel_id in channels:
        try:
            members = await _fetch_channel_members(channel_id)
            await set_members(channel_id, members)
            managers = await _fetch_channel_managers(channel_id)
            await set_managers(channel_id, list(managers))
            await sync_channel(channel_id)
        except Exception as exc:
            logger.warning(f"Failed to init channel {channel_id}", exc_info=exc)


async def periodic_init() -> None:
    while True:
        await asyncio.sleep(3600)  # TODO: allow manual trigger, diff admin, etc.
        try:
            await init_channels()
        except Exception as exc:
            logger.error("Reinit failed", exc_info=exc)


async def main() -> None:
    global ADMIN_ID
    global BOT_USER_ID
    ADMIN_ID = str(_env("ADMIN_ID"))
    BOT_USER_ID = str(await _resolve_bot_user_id(app))
    await ensure_schema()
    await init_channels()

    asyncio.create_task(periodic_init())

    handler = AsyncSocketModeHandler(app, _env("SLACK_APP_TOKEN"))
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
