# Playback on iPhone: what works, what iOS limits

The player (1.3.0) uses its own controls on top of a plain `<video playsinline>`
element, so it behaves the same in a Safari tab and in the Home Screen app.
This page lists what iOS allows, the workarounds the app uses, and how to check
each one on your phone.

> **Test background playback in a normal Safari tab first.** Home Screen web
> apps have historically had extra background-audio restrictions on some iOS
> versions. If it works in Safari but not from the Home Screen icon, that's iOS,
> not the server.

## Screen locked / app in the background

| Situation | What happens |
|---|---|
| **Audio only** on (headphones button) | Plays through an `<audio>` element, which iOS lets keep running when locked. **The reliable way to listen with the screen off.** |
| Watching video, then lock the phone | iOS **pauses `<video>` elements** when Safari goes to the background (unless they're in picture-in-picture). The app catches that moment and continues the *same stream at the same second* through its `<audio>` element. Unlock and it switches back to the video where the audio got to. |
| ...and that handoff gets blocked | Depends on the iOS version: some versions won't let a background page *start* audio. Then the lock screen still shows the video with a play button: **tap play there** and it continues (it's the same audio element, now started by you). |
| Picture-in-picture active | Video keeps playing in the floating window. No handoff needed. |
| Playlist reaches the next video while locked | The next video loads into the *same* media element and starts on its own (no end card, no countdown). |
| Delete prompt at the end while locked | Skipped. The video still counts as finished for you, so a shared video waits for the other person as usual. |

Why the handoff needs a tap first: iOS only lets a media element play by itself
after it has once been started by a tap. The app uses your first tap on the
player to "unlock" its audio element (a silent, instant play and pause).

### Lock screen and Control Center (Media Session API)

* Title, channel and thumbnail artwork, play/pause, **skip back/forward 10 s**,
  and dragging the lock-screen progress bar (seek) all work.
* iOS shows *either* ±skip buttons *or* previous/next-track buttons, never both.
  The video player uses ±10 s. Use the in-app playlist panel for next/previous.
* The Music mini player keeps its own previous/next-track buttons. The video
  player hands the lock screen back to it when you close a video.

## Picture-in-picture

* Button in the player's bottom row (hidden when the browser can't do it).
* iPhone supports it for `<video>` in Safari (iOS 14+). It's not available in
  audio-only mode: the player switches video back on first.
* Swiping home *automatically* enters PiP only when Safari decides to (mostly from
  its native full-screen player). The `autopictureinpicture` hint is set, but
  inline videos may still just pause, so use the PiP button.

## Full screen and rotation

* **Rotate the phone**: the player fills the screen and keeps our controls,
  double-tap skip and captions. Rotate back for details, playlist and transcript.
* The full-screen button opens **Apple's** player (iPhone only allows full screen
  on the video element itself). Our controls and double-tap don't exist there,
  but Apple's have their own skip, AirPlay and captions menu. On iPad the whole
  custom player goes full screen instead.

## Other iOS behaviours

* **Autoplay with sound is blocked** until you tap. A freshly opened video shows a
  big play button. Later playlist videos start on their own.
* **Speed**: Safari resets the speed whenever the source changes, so the app
  re-applies your saved speed for that video. Pitch is preserved.
* **Captions** are drawn by Safari. In Apple's full-screen player they follow
  *Settings → Accessibility → Subtitles & Captioning*.
* **Adding a subtitle file**: the picker opens the Files app. Any file can be
  chosen (no type filter, because iOS greys out `.srt` files with some filters).
  The server rejects anything that isn't SRT or VTT with a short message.
* **Double-tap to zoom** is turned off on the player (`touch-action: manipulation`)
  so double-taps reach the skip gesture.
* **Audio-only still streams the whole mp4** (video and audio are interleaved in
  the file). On home Wi-Fi/Tailscale that's fine. It only saves battery (no
  decoding or screen), not data.
* **Text size**: the app follows *Settings → Display & Brightness → Text Size*
  (and Larger Text in Accessibility) in Safari.

## How to test on your iPhone

Open the app in **Safari** (not the Home Screen icon) for steps 1–4.

1. **Lock-screen handoff**: start a video, tap play, press the side button.
   Audio should keep going. Unlock: the video picks up where the audio is.
   If it stopped, open the lock screen and press play once.
2. **Audio only**: tap the headphones button, lock the phone. It keeps playing.
   Try the ±10 s buttons and dragging the lock-screen progress bar.
3. **Control Center**: swipe down from the top-right while playing. You'll see
   the title and thumbnail, and play/pause works.
4. **Playlist while locked**: play a playlist in audio-only mode, skip near the
   end of a video, lock. The next one should start by itself.
5. **PiP**: tap the PiP button, then go to the Home Screen. A floating video
   window keeps playing.
6. Repeat 1–2 from the **Home Screen app** if you use it.
