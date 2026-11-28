# Changelog

What changed in each version of Altera, for the people using it. The version is shown at the bottom of Settings.

## 1.0.1 (2026-11-27)

Fixes after 1.0.0, a safer Setup, and an easier first chat.

- **Setup keeps Altera working when a download fails.** The memory and microphone models and the local Kokoro voice are
  unticked unless you tick them. Altera is installed and ready for text chat before any of them starts; if one fails, Setup
  names it, Altera still opens, and running Setup again with that download ticked tries it again.
- **The first chat comes first.** After you connect a chat model, **Finish and choose a starter** takes you to the starter
  characters. Adding one opens a text chat with them, with a link to check the connection if there is no reply, and after
  the first reply Altera says where to choose a voice.
- **No cloned voices at the optional levels.** While an optional content level is on, Altera does not speak with a Voice Lab
  voice cloned from a person's recording, nor with a Cast voice whose origin Voice Lab does not report (Voice Lab 1.0.0
  and older). The reply stays as text and says why. Voices designed from words and the models' own voices still speak.
- An Altera that Setup.exe installed says to run Setup again and tick the local voice, or to use Voice Lab, where it said
  to run `voice\setup.bat`.
- Choosing a model names the LFM Open License's limit: free for companies under US$10 million in annual revenue.
- The issue forms match the other Fel's Studio apps', and questions go to Discussions.

## 1.0.0 (2026-11-26)

The first release. Altera is a companion app that runs on your own PC. You chat with pals and characters you make,
through a chat model you run yourself in LM Studio or Ollama, and the conversations, memories and notes stay in a folder
on your machine.

- **A Windows installer.** `FelsAltera-Setup-1.0.0.exe` brings its own Python, so nothing else needs installing first.
  It can also set up the memory and microphone models and the local Kokoro voice. In Settings, **Check for updates**
  finds a newer release when you ask, **Update now** installs it, and **Undo** goes back to the version before.
  [Windows setup and updates](docs/SETUP.md#windows-installer)
- **Help getting a chat model running.** Setup tells "not installed" apart from "installed but its server is off", and
  can start LM Studio's or Ollama's server after you say yes. It suggests one of the two models that did best on
  Altera's model test, Gemma 4 12B or LFM3 1.7B, by this PC's memory, and with Ollama it can download it, with
  progress and Stop. [Choosing a chat model](docs/CHOOSING_A_MODEL.md) has the test's results.
- **Two starter characters.** Ottilie Mere and Lorraine Malone, made with NPC Forge, are offered on Home with their
  pictures and a voice designed for Voice Lab. Nothing is added until you choose **Add**, and adding one goes through
  the same checks as importing a card.
- **Part of Fel's Studio,** four free apps that work together:
  - pals can speak with a ready voice from [Voice Lab](https://github.com/Fel101/fels-voice-lab), including the
    voices cast there from your own recordings, with each voice's licence shown;
  - characters sent from [NPC Forge](https://github.com/Fel101/fels-npc-forge) wait on Home as character cards, with
    their portrait, expressions, living portrait and voice, until you import or dismiss them;
  - **Meet someone new** rolls a companion in NPC Forge's Modern life setting, with a choice of gender;
  - the apps share the GPU: a light in the header shows which app is using it, and a reply waits, with a line saying
    why, while NPC Forge has the GPU to itself.
- **A new port.** New installs use `http://127.0.0.1:8767`. An existing `config.yaml` keeps its port, so a phone set up
  through Tailscale keeps working.
- **Everything in Altera itself is non-sexual.** Kids mode, the parent PIN and the kids view are as before. A setting
  from earlier copies of the source that is not part of the core now belongs to a separate add-on, and your saved
  data is kept.
- **Docs in plain words.** The [user guide](docs/README.md) was rewritten, with a page on
  [how Altera keeps things safe](docs/SAFETY.md) and one on [responsible use](docs/responsible-use.md).
