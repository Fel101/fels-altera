# Choosing a chat model

Altera's replies come from the chat model you run in LM Studio or Ollama, so the model decides how quickly a pal answers
and how well they keep to who they are. First-run setup suggests one from this PC's memory:

| Your PC | Suggested model | Ollama name | Licence |
| --- | --- | --- | --- |
| A graphics card with 10 GB or more and 16 GB of RAM | Gemma 4 12B | `gemma4:12b-it-q4_K_M` (8.0 GB) | Apache 2.0 |
| A smaller graphics card, or 8 GB of RAM and no graphics card | LFM3 1.7B | `lfm3:1.7b-q8_0` (1.8 GB) | LFM Open License v1.0 |

In LM Studio, Gemma 4 12B is [google/gemma-4-12b](https://lmstudio.ai/models/google/gemma-4-12b) in its catalog, and
LFM3 1.7B downloads from [LiquidAI/LFM3-1.7B-GGUF](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF) on Hugging Face (choose
the Q8_0 file). The LFM Open License allows personal use, but commercial use by a company above Liquid AI's threshold is
not covered, so read it before you use LFM3 for work.

The suggestions come from the test below. Any model that answers the short test in Setup works; these are the ones that
did best at the things Altera asks of a model.

## The test

Every model answered the same 28 conversations in LM Studio, four of each kind, with the same 8,192-token limit on each
answer that Altera uses. Another local model, Qwen3.8 27B, graded each answer from 1 to 5. The kinds that apply to Altera
itself are shown here: playing a character, remembering what it was told, following instructions, using a tool, keeping
track of a long conversation, and keeping to itself when a message tries to give it new instructions.

All of it ran on one PC: an AMD Radeon AI PRO R9700 with 32 GB of graphics memory, on 2026-11-23. On a smaller card a
model answers more slowly, and a model that does not fit in graphics memory slows down a great deal.

| Model (download) | Memory used | First words after | Speed | Quality (0-10) | For Altera |
| --- | --- | --- | --- | --- | --- |
| LFM3 1.7B, Q8_0 (1.9 GB) | 1.8 GB | 5.6 s | 151 tokens/s | 8.3 | Great |
| Spark-X2.5 4B, Q8_0 (4.4 GB) | 4.2 GB | 8.1 s | 87 tokens/s | 8.6 | Good |
| Gemma 4 12B, Q4_K_M (7.8 GB) | 7.4 GB | 8.1 s | 44 tokens/s | 9.3 | Good |
| Qwen3.8 27B, Q4_K_M (17.7 GB) | 16.9 GB | 6.1 s | 43 tokens/s | 8.8 * | Good |
| Qwen3.5 9B, Q4_K_M (6.6 GB) | 6.2 GB | 20.4 s | 71 tokens/s | 9.5 | Not fit: too slow to start |
| Qwen3.6 35B-A3B, Q6_K (31.1 GB) | 29.7 GB | 17.7 s | 43 tokens/s | 9.4 | Too slow |
| Qwen3.5 0.8B, Q4_K_M (0.7 GB) | 0.7 GB | 11.1 s | 156 tokens/s | 5.4 | Usable |

\* The grader does not grade its own answers, so Qwen3.8 27B's quality comes from an earlier run of 13 answers graded by
another model.

- **First words after** is the median time until the first words of an answer appear. It matters most in a chat. A model
  that thinks before it answers, as Qwen3.5 9B does, can score well and still keep you waiting.
- **Quality** turns the grades into 0 to 10: a grade of 1 is 0, 3 is 5, and 5 is 10.
- **For Altera:** Great means the first words within 6 seconds at 60 tokens a second or more, with no failed or empty
  answers. Good means within 9 seconds at 25 tokens a second or more. Usable means within 14 seconds at 10 or more. A
  model is not fit when its first words take over 20 seconds, it is under 3 tokens a second, or a quarter of its answers
  fail or come back empty. Anything else is too slow.

Grades by kind of conversation (1 to 5):

| Model | Character | Memory | Instructions | Tools | Long conversation | Keeping to itself |
| --- | --- | --- | --- | --- | --- | --- |
| LFM3 1.7B | 3.5 | 5.0 | 5.0 | 5.0 | 5.0 | 3.0 |
| Spark-X2.5 4B | 3.25 | 5.0 | 5.0 | 5.0 | 5.0 | 4.25 |
| Gemma 4 12B | 4.5 | 5.0 | 5.0 | 5.0 | 5.0 | 3.5 |
| Qwen3.8 27B * | 4.5 | 5.0 | 5.0 | 4.5 | 5.0 | 3.0 |
| Qwen3.5 9B | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 3.75 |
| Qwen3.6 35B-A3B | 4.75 | 5.0 | 5.0 | 5.0 | 5.0 | 3.5 |
| Qwen3.5 0.8B | 1.5 | 3.5 | 4.0 | 5.0 | 4.0 | 1.5 |

Every model was weakest at playing a character and at keeping to itself. The smallest, Qwen3.5 0.8B, was weak at both and
ran out of room while still thinking on 6 of its 28 answers.

## What the numbers can't tell you

- **Four conversations of each kind is a small test.** A difference of a few tenths is not a real difference.
- **One model graded the rest.** Its grades are an estimate, checked against a second grader on 78 answers (82% the same,
  96% within one point).
- **Speed is from one PC.** Divide the speeds by a lot on a laptop or a CPU, and look at the memory column first: the model
  has to fit in your graphics memory, with room to spare, to answer quickly.
- **Check each model's licence** on its page before you share anything made with it. The Qwen, Spark-X2.5 and Gemma
  models in the test are under their own licences, not Altera's.
