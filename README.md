# CHANNELBLAM

CHANNELBLAM allows you to manage your channel with a variety of features, all done without any admin APIs. Currently, it supports IDV locking, slowmode, positivity filters, and individual user BLAMs, all utilizing channel posting permissions.

## How to Use

Invite @BLAMV2 (a bot) to your channel. It should automatically invite the user @BLAMMER, but if it doesn't, do that manually. Then, grant @BLAMMER Channel Manager to allow it to change channel posting permissions.

Lock your channel to IDV with `/blam idv required`.

Would you like to exempt users from the IDV block? Run `/blam whitelist`.

You can enable slowmode with `/blam slowmode`.

BLAM someone in particular with `/blam user blam @user`.

And finally, if you'd like to make sure everybody's being nice, you can turn on the positivity filter with `/blam positivity on`. Configurable penalty times are coming soon.

## TODO

- Reimplement `/blam idv test`
- More slowmode configuration (e.g. top-level restrictions only)
- Fix bot posting permissions/workarounds
- Better error handling
- Reworked command parsing/subcommands

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

export HCAI_API_KEY=sk-hc-v1-... # for positivity filter, optional
```

```sh
uv sync && uv run main.py
```

## Notes

This is a reworking of (https://github.com/sadeshmukh/channelblam).

The previous version of CHANNELBLAM directly interfaced with Slack - acting on user joins and leaves. This meant I had to interact with a lot less data directly. However, it also made a lot of other things (including additional new features to come) very difficult to work with.

The new CHANNELBLAM introduces a layer in between Slack operations and commands. Commands trigger internal state updates, which are reflected through periodic or manual syncs through Slack. This keeps performance much better, as it means there's an extra layer of assuredness when dealing with users. It will mean a few more events must be tracked, but that's a small price to pay for the possibility of more comprehensive channel settings and easier development.

CHANNELBLAM has not been tested beyond 100 users, and there is a very large chance that some operations will be slow or unresponsive. If you'd like to help me test, please let me know - since CHANNELBLAM doesn't kick users, there should be a low risk of accidental problems created.
