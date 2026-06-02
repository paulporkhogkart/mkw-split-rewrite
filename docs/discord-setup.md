# Discord Rich Presence — Setup (one-time)

Three steps: create a Discord application, give us its ID, and upload the images.

## 1. Create the application
1. Go to https://discord.com/developers/applications and sign in.
2. Click **New Application**, name it exactly **Mario Kart World**, agree, **Create**.
3. On **General Information**, copy the **Application ID** (an ~18-19 digit number). This is the one value we need from you. It is *not* secret — no bot token or OAuth is required for Rich Presence.

## 2. Upload the art assets
1. From the repo root, run: `python scripts/fetch_discord_assets.py`
   - Downloads every course icon plus the penguin and splash into `out/discord-assets/` as **512x512 PNGs** (Discord's minimum size), named by asset key (`rainbow_road.png`, `penguin.png`, `splash.png`, ...).
2. In the Developer Portal: open your app → left sidebar **Rich Presence** → **Art Assets** → **Add Image(s)**.
3. Drag in **all** files from `out/discord-assets/`. Discord uses the **filename (lowercased, no extension)** as the asset key, so they line up automatically with what the app references.
4. **Save Changes.** Assets can take a few minutes to process.

## 3. Hand off the Application ID
Paste the Application ID into `src-tauri/src/discord.rs` (the `DISCORD_APP_ID` constant), or send it to your assistant. Until it's set, the feature stays silent (no errors).

## Notes
- In Discord: **User Settings → Activity Privacy → Display current activity** must be on.
- You may not see your *own* presence buttons (e.g. "Watch on Twitch") in your own client — a known Discord quirk; others see them.
- Set your Twitch URL in the app's **Settings → Discord** to enable the button.
