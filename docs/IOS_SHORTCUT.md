# iOS share-sheet shortcut: "Send to Home Videos"

Send a video or song from the YouTube / YouTube Music app (or Safari) straight
to the home server with two taps: **Share → Send to Home Videos → pick where it
goes**. A notification confirms it; the app doesn't need to open.

Home Screen web apps can't appear in the iOS share sheet, so this uses the
Shortcuts app instead. The app shows these same steps with your server address
and profile already filled in: **Settings → Share-sheet shortcut**.

You build it once per iPhone (about 3 minutes). iOS only imports shortcut files
that were signed on a Mac or shared via iCloud, so there's no file to download.
Once one of you has built it, you can share it to the other phone from the
Shortcuts app (long-press → Share) and they just change the profile name.

## Build it

1. **Shortcuts → +**. Name it **Send to Home Videos**.
2. Tap **(i)** → turn on **Show in Share Sheet**. In the editor, tap **Receive
   Any input** and keep only **URLs** and **Text**. Set **If there's no input** to
   **Get Clipboard** (so running it from the Shortcuts app uses a copied link).
3. Add **Choose from Menu**, prompt "Send as…", with three items. Inside each item
   add a **Text** action containing exactly:

   | Menu item | Text |
   |---|---|
   | Video – My queue | `video-personal` |
   | Video – Shared | `video-shared` |
   | Music (MP3) | `mp3` |

4. After **End Menu**, add **Get Contents of URL**:
   * URL: `https://<your-server>.<your-tailnet>.ts.net/api/share`
     (the address from `tailscale serve`, plus `/api/share`)
   * Show More → Method **POST**, Request Body **JSON**, three **Text** fields:
     * `url` → the **Shortcut Input** variable
     * `profile` → your profile name exactly as in the app, e.g. `Alex`
     * `action` → the **Menu Result** variable
5. Add **Get Dictionary Value**: Value for `message` in **Contents of URL**.
6. Add **Show Notification** with the **Dictionary Value** variable.

## Use it

YouTube app → **Share** → **More (…)** → **Send to Home Videos** → choose.
Tip: in the share sheet, **Edit Actions** → add it to Favourites so it's always
in the first row.

You'll see e.g. *Downloading: "How to make a sourdough starter" → My queue (Alex)*.
Errors come back the same way (e.g. *No profile called 'justn'*, *not enough
disk space*).

## Notes

* Tailscale must be connected on the phone.
* Videos use the default quality from Settings; music uses the default music format
  unless the action says `mp3` or `m4a`.
* Other actions the server accepts: `video-none` (download without queueing), `m4a`.
* `profile` can be the name (any capitalisation) or the number 1/2.

## The API (for tinkering)

```
POST /api/share
{"url": "https://youtu.be/…", "profile": "Alex", "action": "video-personal"}
→ 200 {"ok": true, "message": "Downloading: “…” → My queue (Alex)", "video_id": "…", "status": "queued"}
→ 4xx/5xx {"ok": false, "message": "…why…"}
```

Text around the link is fine (`"Look at this https://youtu.be/…?si=…"`).
