# MacroDroid Setup Guide

This folder contains the MacroDroid export file (`MacroDroid.mdr`) that includes all macros, variables, and custom widgets for the Signal Bot trading automation.

## Transferring the `.mdr` File to Android

Before you can import the macro file into MacroDroid, you need to transfer `MacroDroid.mdr` from your computer to your Android device. Here are several methods:

### Method 1: USB File Transfer (Easiest)
1. Connect your Android device to your computer via USB cable.
2. Enable **File Transfer** mode on your Android device (swipe down, tap USB notification, select "File Transfer").
3. On your computer, open the Android device folder (File Explorer on Windows or Finder on macOS).
4. Navigate to your device's internal storage or an SD card.
5. Create a folder called `MacroDroid` (if it doesn't exist) or use an existing folder (e.g., `Documents`).
6. Copy `MacroDroid.mdr` from this repository folder to that location on your device.
7. Disconnect and proceed to import (see below).

### Method 2: Cloud Storage (Google Drive, Dropbox, OneDrive)
1. Upload `MacroDroid.mdr` to your cloud storage account (Google Drive, Dropbox, OneDrive, etc.).
2. On your Android device, open the cloud storage app.
3. Download the file to a local folder (e.g., Downloads, Documents, or a custom folder).
4. Proceed to import (see below).

### Method 3: Email
1. Attach `MacroDroid.mdr` to an email and send it to yourself.
2. On your Android device, open the email and download the attachment to your device's storage.
3. Proceed to import (see below).

### Method 4: Local Network (if on same WiFi)
1. Use a tool like **Syncthing**, **Nextcloud**, or a simple HTTP server to share the file over your local network.
2. Download the file from your Android device.
3. Proceed to import (see below).

## Importing into MacroDroid

Once `MacroDroid.mdr` is on your Android device:

1. Open the **MacroDroid** app.
2. If the intro screen appears, skip it.
3. Press the **Home button** (bottom-left corner of the app).
4. Select **Import** from the menu.
5. Navigate to the folder where you saved `MacroDroid.mdr` and select it.
6. Grant any permission prompts.
7. The macro file will be imported and you should see the macros, variables, and custom widgets available in the app.

## Updating Global Variables

After importing, you **must** update the following global variables before running any macros:

1. In MacroDroid, press the **Variables** button.
2. Update these variables:
   - **`tunnel_url`** : the stable public URL of your webhook, including the path (for example `https://<your-assigned-name>.ngrok-free.app/trade_signal`). It must stay the same across restarts, so use a tunnel that provides a stable URL.
   - **`webhook_secret`** : the same value you set as `WEBHOOK_SECRET` in the server `.env`. The HTTP request action must send it as a header named `x-webhook-secret`, otherwise the server returns `401`.
   - **`signal_provider`** : (optional) your signal provider name
   - **`timezone`** : must be in pytz format (e.g., `Etc/GMT-2` for GMT+2)

3. Save and close.

Note the timezone sign convention: pytz uses the inverted offset. `Etc/GMT-2` is **GMT+2**, and
`Etc/GMT+4` is GMT-4. Getting this wrong shifts every entry time.

If your tunnel is ngrok's free tier, you can also add the header `ngrok-skip-browser-warning` with any value. Programmatic API requests are not affected by the browser interstitial, but the header removes any doubt.

## Configuring the HTTP Request Action

The macro that forwards a notification must send an HTTP request configured as follows:

| Setting | Value |
| --- | --- |
| Method | `POST` |
| URL | the `tunnel_url` variable, ending in `/trade_signal` |
| Body / content type | raw text (`text/plain`) containing the notification text |
| Header | `x-webhook-secret` = the `webhook_secret` variable |

Requirements:

- The server must be running and reachable at `tunnel_url`. See
  [Running as a service](../README.md#running-as-a-service-survives-restarts) in the main README so
  the app and the tunnel restart automatically after a reboot.
- Use a tunnel that provides a **stable** URL. A tunnel that assigns a new random URL on each start
  (for example `localtunnel`) forces you to re-edit `tunnel_url` after every restart.
- ngrok agent **v3** is required for stable free dev domains. The discontinued v2 agent cannot
  provide one.

The server accepts a signal only if it contains an asset, a direction, an entry time, a provider,
and a timezone. Provider and timezone fall back to the server configuration when omitted.

## Custom Widgets

Once imported, you can add the following custom widgets to your Android home screen:
- **`test signal`** : sends a test webhook to your server
- **`go to web ui`** : opens the web UI at `http://localhost:<PORT>/ui/`
- **`quick view`** : displays account balance and PnL info

## Troubleshooting

- **File not found**: Make sure `MacroDroid.mdr` is in a location accessible by MacroDroid (typically Downloads, Documents, or a custom folder).
- **Import fails**: Try re-downloading the file or clearing MacroDroid cache and trying again.
- **Macros don't run**: Ensure all global variables are set correctly, especially `tunnel_url` and `timezone`.
- **HTTP 401 from the server**: `webhook_secret` in MacroDroid does not match `WEBHOOK_SECRET` in the server `.env`, or the header is not named `x-webhook-secret`. Check `GET /health` - it reports `webhook_auth_configured` and an `unauthorized` counter.
- **HTTP 503 from the server**: the app is running but the broker is not connected. Check `broker_connected` in `GET /health` and re-capture the Pocket Option session.
- **HTTP 403 from the server**: the daily loss limit was breached, so processing is halted by design.
- **Signals accepted but no trade placed**: the signal arrived after its entry time. Check the timezone convention above.
- **Signals stop arriving after a tunnel restart**: the tunnel URL changed. Use a tunnel with a stable URL and update `tunnel_url` once; check `last_received_at` in `GET /health`.

For more help, refer to the main project `README.md` in the parent directory.
