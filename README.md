# CHANNELBLAM

CHANNELBLAM allows you to lock your channel with a variety of features. Currently, it supports IDV locking and slowmode utilizing channel posting permissions.

## How to Use

Invite @BLAMV2 to your channel. It should automatically invite @BLAMMER, but if it doesn't, do that manually. Then, grant @BLAMMER Channel Manager to allow it to change channel posting permissions.

Lock your channel to IDV with `/blam idv`.

Would you like to exempt users from the IDV block? Run `/blam whitelist`.

You can enable slowmode with `/blam slowmode`.

## TODO

Features on the roadmap:

- Reimplement `/blam idv test` and `/blam whitelist channel`
- More slowmode configuration (e.g. top-level restrictions only)
- Configurable penalty length
- Fix bot posting permissions

# Development

Set the following environment variables:

```sh
export SLACK_APP_TOKEN=xapp-...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=your-signing-secret
export SLACK_PERSONAL_TOKEN=xoxp-... # moving away from this one in the future
export SLACK_XOXC=xoxc-...
export SLACK_XOXD=xoxd-...
export ADMIN_ID=U...
```

```sh
uv sync && uv run main.py
```

## Notes

This is a reworking of (https://github.com/sadeshmukh/channelblam).

The previous version of CHANNELBLAM directly interfaced with Slack - acting on user joins and leaves. This meant I had to interact with a lot less data directly. However, it also made a lot of other things (including additional new features to come) very difficult to work with.

The new CHANNELBLAM introduces a layer in between Slack operations and commands. Commands trigger internal state updates, which are reflected through periodic or manual syncs through Slack. This keeps performance much better, as it means there's an extra layer of assuredness when dealing with users. It will mean a few more events must be tracked, but that's a small price to pay for the possibility of more comprehensive channel settings and easier development.
