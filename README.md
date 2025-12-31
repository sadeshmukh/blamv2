This is a reworking of (https://github.com/sadeshmukh/channelblam).

The primary internal difference is the method of updating. The previous version of CHANNELBLAM directly interfaced with Slack - acting on user joins and leaves. This meant I had to interact with a lot less data directly. However, it also made a lot of other things (including additional new features to come) very difficult to work in.

The new CHANNELBLAM introduces a layer in between Slack operations and commands. Commands trigger internal state updates, which are reflected through periodic or manual syncs through Slack. This keeps performance much higher, as it means there's an extra layer of assuredness when dealing with users. It will mean a few more events must be tracked, but that's a small price to pay for the possibility of more comprehensive channel settings and control over settings.
