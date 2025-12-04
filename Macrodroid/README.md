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
   - **`ngrok_url`** : set to your Ngrok public forwarding URL (e.g., `https://<your-id>.ngrok.io`)
   - **`signal_provider`** : (optional) your signal provider name
   - **`timezone`** : must be in pytz format (e.g., `Etc/GMT-2` for GMT+2)

3. Save and close.

## Custom Widgets

Once imported, you can add the following custom widgets to your Android home screen:
- **`test signal`** : sends a test webhook to your server
- **`go to web ui`** : opens the web UI at `http://localhost:<PORT>/ui/`
- **`quick view`** : displays account balance and PnL info

## Troubleshooting

- **File not found**: Make sure `MacroDroid.mdr` is in a location accessible by MacroDroid (typically Downloads, Documents, or a custom folder).
- **Import fails**: Try re-downloading the file or clearing MacroDroid cache and trying again.
- **Macros don't run**: Ensure all global variables are set correctly, especially `ngrok_url` and `timezone`.

For more help, refer to the main project `README.md` in the parent directory.
