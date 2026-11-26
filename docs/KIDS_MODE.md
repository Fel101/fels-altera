# Kids mode

Kids mode gives a pal simpler words and child-friendly topics. Use parental controls and adult supervision
alongside it. The topic checks can miss things, and the model can still give a wrong answer.

## Setting it up

1. On **Home**, open **Settings** under any pal and choose **Set up kids mode…**.
2. Read the notice, choose a **parent PIN** of 4 to 8 digits and confirm it. Choose **Enable kids mode**.
3. Turn on **Kids mode for this pal**. The switch is also available when you make a new pal.

Home and chat show a **Kids mode** badge for those pals. Changing a pal's switch needs the parent PIN.
If you see a message about the separate add-on, remove that add-on and restart Altera before setting up Kids mode.

To turn off Kids mode for this Altera, switch it off for each pal, then choose **Lock kids mode** under
**About kids mode**. You will need the PIN.

## The app lock and the kids view

On a child's device, choose **Kids** on Altera's lock screen. That browser can open only Kids mode pals.
Settings, other companions, saved memory controls and search stay in the main app. The child can open a pal
without changing who is open on the parent's device.

Enter the PIN to unlock the main app. If several PIN attempts fail, wait for the lock screen to let you try again.
Anyone who knows the PIN or can edit Altera's files can unlock the app. Keep the PIN private.

## What changes for a pal

- Altera asks the model to use simple words and child-friendly topics.
- Messages about self-harm, sexual content, romance aimed at the pal, drugs, alcohol, smoking, violence, gambling
  or personal details may get a fixed reply. Altera checks the pal's answer before showing or speaking it.
- Web search, check-ins, new chat pictures and the flirty living-portrait expression are off. Existing pictures
  remain in chat history until removed. A pal can still have a [portrait](PORTRAITS.md).
- Memory, Notes and Story stay off by default. A parent can review those switches in the pal's settings.

[How the Kids view and topic checks work](SAFETY.md#kids-mode)

## A forgotten PIN

On the PC that runs Altera, stop the app and open a terminal in its folder. Run the command with your instance folder.

```powershell
.\.venv\Scripts\python.exe scripts/reset_pin.py --home "C:\path\to\the\instance"
```

Start Altera again and set a new PIN under **Home → Settings → About kids mode**. Your chats and each pal's Kids mode
setting stay in place. [App lock API](API_REFERENCE.md#app-lock-and-kids-view)
