# Synthetic demo and product-asset capture

Repository screenshots and demos must come from the real application and must
never contain personal usage, account identifiers, local paths, credentials,
cookies, tokens, or raw provider payloads.

## Run the safe demo

```bash
python3 scripts/usage_widget.py --demo
```

Demo mode:

- uses synthetic in-memory snapshots;
- enables Cursor, Claude Code, and Codex for a representative first view;
- never constructs the normal provider collector;
- does not read provider or application-data files;
- does not start provider processes;
- does not make network requests; and
- keeps settings changes in memory.

The footer displays **Synthetic demo · no provider access** so captured assets
cannot be mistaken for live account data.

## Capture checklist

1. Use the detailed view for `docs/assets/dashboard.png`.
2. Use the plus/minus control and capture compact mode as
   `docs/assets/dashboard-compact.png`.
3. Open Settings and capture the provider permissions as
   `docs/assets/settings.png`.
4. Crop to the application window only.
5. Confirm the synthetic-demo footer is visible in dashboard captures.
6. Inspect the images at full resolution before committing them.

## Generate the README GIF

With FFmpeg installed, run:

```bash
ffmpeg -y \
  -loop 1 -t 3 -i docs/assets/dashboard.png \
  -loop 1 -t 3 -i docs/assets/dashboard-compact.png \
  -loop 1 -t 3 -i docs/assets/settings.png \
  -filter_complex \
  "[0:v]scale=680:-2:flags=lanczos,pad=720:720:(ow-iw)/2:(oh-ih)/2:color=0x0A0E14,setsar=1[a];\
[1:v]scale=600:-2:flags=lanczos,pad=720:720:(ow-iw)/2:(oh-ih)/2:color=0x0A0E14,setsar=1[b];\
[2:v]scale=-2:680:flags=lanczos,pad=720:720:(ow-iw)/2:(oh-ih)/2:color=0x0A0E14,setsar=1[c];\
[a][b]xfade=transition=fade:duration=0.5:offset=2.5[ab];\
[ab][c]xfade=transition=fade:duration=0.5:offset=5.0,fps=12,split[s0][s1];\
[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
  -t 8 -loop 0 docs/assets/demo.gif
```

Keep the GIF below 10 MB and visually inspect both states after generation.
