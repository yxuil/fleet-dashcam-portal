# Samples

Drop sample dashcam MP4 files in this directory (any name, just `*.mp4`)
to seed the dev database with realistic clip bytes you can actually play
back in the portal.

Suggested format
- short, ~30–180 seconds
- H.264 / AAC in an MP4 container
- a handful of files is plenty — the seed script rotates through them
  round-robin

No sample files are committed to the repo (they'd bloat the clone). The
seed script handles an empty directory gracefully:

- with samples present and `--upload-samples` (default), each clip row's
  `storage_key` is PUT to MinIO using one of the sample MP4s round-robin
- with no samples (or `--no-upload-samples`), clip rows still get a
  canonical `storage_key`, but the underlying object isn't created —
  playback will return a 404 from MinIO until you upload something

You can grab any short MP4 (e.g. an old phone clip) and rename it
`samples/dash-001.mp4` to get going.
