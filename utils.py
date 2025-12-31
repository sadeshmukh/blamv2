import logging
import re
import aiohttp
from slack_sdk.errors import SlackApiError
import os
import json

logger = logging.getLogger("utils")

_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


def _parse_mention(token: str) -> str | None:
    if not (token.startswith("<@") and token.endswith(">")):
        return None
    inner = token[2:-1]
    user_id = inner.split("|", 1)[0]
    if not _USER_ID_RE.match(user_id):
        return None
    return user_id


def _env(name: str) -> str:
    if not (value := os.getenv(name)):
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _cookie_header() -> str:
    xoxd = _env("SLACK_XOXD").replace("%2F", "/").replace("%3D", "=")
    cookie = f"d={xoxd};"
    if extra := os.getenv("SLACK_X_COOKIE"):  # testing material, not necessary
        cookie = f"{cookie} x={extra};"
    return cookie


XOXC_TOKEN = _env("SLACK_XOXC")
HEADERS = {
    "Cookie": _cookie_header(),
    "Origin": "https://app.slack.com",
    "Referer": "https://app.slack.com/client",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
ADMIN_ID = _env("ADMIN_ID")


async def _resolve_bot_user_id(app) -> str:
    try:
        auth_info = await app.client.auth_test()
        user_id = auth_info.get("user_id")
        if not user_id:
            raise Exception("Unable to resolve bot user id")
        logger.info(f"Bot user id resolved as {user_id}")
        return user_id
    except SlackApiError as exc:
        logger.error("auth_test failed", exc_info=exc)
        raise


async def _kick_xoxc(channel_id: str, user_id: str, logger) -> None:
    token_xoxc = _env("SLACK_XOXC")
    url = "https://hackclub.enterprise.slack.com/api/conversations.kick?slack_route=E09V59WQY1E%3AE09V59WQY1E"
    async with aiohttp.ClientSession() as session:
        formdata = {"channel": channel_id, "user": user_id, "token": token_xoxc}
        cookie = f"d={_env('SLACK_XOXD').replace('%2F', '/').replace('%3D', '=')};"
        headers = {"Cookie": cookie}
        session.headers.update(headers)
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                logger.warning(f"Kick xoxc failed: {data}")


async def _fetch_channel_managers(channel_id: str) -> list[str]:
    managers: list[str] = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        formdata = {
            "token": XOXC_TOKEN,
            "entity_id": channel_id,
        }

        async with session.post(
            "https://hackclub.enterprise.slack.com/api/admin.roles.entity.listAssignments?slack_route=E09V59WQY1E%3AE09V59WQY1E",
            data=formdata,
        ) as resp:
            data = await resp.json()
            if not data.get("ok", False) and data.get("error"):
                raise RuntimeError(
                    f"admin.roles.entity.listAssignments failed: {data.get('error', '??')}"
                )

            role_assignments = data.get("role_assignments") or []
            first = role_assignments[0] if role_assignments else {}
            users = first.get("users") if isinstance(first, dict) else None
            if not users or not isinstance(users, list):
                logger.warning("what the goof??", extra={"data": data})
                return []
            managers.extend(u for u in users if isinstance(u, str))

    return managers


async def _get_channel_allowed_users(channel_id: str) -> list[str]:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.get?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "pref_name": "who_can_post",
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.get failed: {data.get('error', '??')}"
                )
            pref = data.get("pref_value", {})
            users = pref.get("user", [])
    return users


async def _set_channel_allowed_users(channel_id: str, users: list[str]) -> None:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = "https://hackclub.enterprise.slack.com/api/channels.prefs.set?slack_route=E09V59WQY1E%3AE09V59WQY1E"
        prefs = {
            "who_can_post": (
                "type:admin,user:" + ",user:".join(users) if users else "type:admin"
            ),
            "can_thread": (
                "type:admin,user:" + ",user:".join(users) if users else "type:admin"
            ),
            "enable_at_here": "true",
            "enable_at_channel": "true",
        }
        formdata = {
            "token": XOXC_TOKEN,
            "channel_id": channel_id,
            "prefs": json.dumps(prefs),
        }
        async with session.post(url, data=formdata) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(
                    f"channels.prefs.set failed: {data.get('error', '??')}"
                )


async def _fetch_channel_members(channel_id: str) -> list[str]:
    members: list[str] = []
    marker = None
    headers = {**HEADERS, "Content-Type": "application/json;charset=UTF-8"}
    payload_base = {
        "token": XOXC_TOKEN,
        "enterprise_token": XOXC_TOKEN,
        "include_profile_only_users": True,
        "count": 500,
        "channels": [channel_id],
        "filter": "people",
        "index": "users_by_display_name",
        "locale": "en-US",
        "present_first": False,
        "fuzz": 1,
        # idk what any of these do I just toss em in
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            payload = {**payload_base}
            if marker:
                payload["marker"] = marker

            async with session.post(
                "https://edgeapi.slack.com/cache/E09V59WQY1E/users/list", json=payload
            ) as resp:
                data = await resp.json()
                if error := data.get("error"):
                    raise RuntimeError(f"edge users.list failed: {error}")

                results = data.get("results", []) or []
                for user in results:
                    if user_id := user.get("id"):
                        members.append(user_id)

                marker = data.get("next_marker")
                if not marker or not results:
                    break

    return members
