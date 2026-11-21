# Using Echo

This guide walks through the app screen by screen. For installing it, see the
[README](../README.md); for how the measurements work and how they were
checked, see the [method](method.md).

## Voices

Everything belongs to a **voice**: the person or character you're imitating.
A voice holds the lines you practise and the recordings they came from, such
as a long video of one person talking or a compilation of a game character's
voice lines. The sidebar lists the voices, and nothing else.

A voice's page has three tabs:

- **Practise:** the voice score, your lines, and a summary of the voice.
- **Progress:** every take as a dot (one colour per line), the average of the
  last five, and each part over your last five takes against your first five.
- **Add lines:** suggested lines, links, uploads, recordings and speakers. A
  voice with no lines yet opens here.

**The voice at a glance.** Each recording summarises the voice from its
lines: typical pitch and note, usual range, pace in words a second, and how
breathy it is. Read speech is voiced on 53–60% of its word frames; a soft,
breathy voice on about 45%.

**You against it.** Once you've practised a few of its lines, the same card
shows your habits across all of them, from your last 10 takes, for example
"3.0 semitones low (you average 170 Hz, F3), darker (formants 11% lower)".
Habits that show up in every line are the first to fix.

## Getting lines from a video

**Paste a link** in the box on the first screen, at the top of home, or in
"+ New voice" (it takes a name or a link). Echo makes a voice for it straight
away and opens its page, where a progress card shows each step. A link pasted
on a voice's own page adds to that voice. Uploading a file over a minute long
goes through the same steps; a shorter file becomes one line.

A background job does the work:

1. **Download:** links go through yt-dlp, which covers YouTube and most video
   sites. Live streams are refused, since they have no end to wait for.
2. **Read:** the audio is decoded to 16 kHz mono.
3. **Transcribe:** Whisper base.en transcribes it with word timestamps, after
   voice-activity detection drops stretches with no speech.
4. **Split:** lines end at full stops and at pauses of 0.7 s or more. Lines
   over 10 s are split at a comma or pause near the middle.
5. **Speakers:** the lines are grouped by who is speaking (below).
6. **Pick:** the voice gets the five best lines of its speaker, enough for a
   voice check.

A 25-minute video took 75 seconds from pasting the link to five picked lines.
The job runs at below-normal priority, keeps going if the page is closed, and
starts again if the computer stops part-way.

**Naming.** Once the video is known, the voice is named after its
**channel**, which for most creators is the person. A second video from a
channel that already has a voice joins that voice, with a few of its own lines
picked, rather than making a second voice with the same name. When the site
gives no channel, the voice is named after the title, without [tags],
(asides) or anything after " | " or " - ". The page offers the other name
once: the title for a channel-named voice (for a fan's compilation of a
character, "Ellie - All Voice Lines | The Last of Us Part II" gives "Ellie"),
or the channel for a title-named one. A name you type yourself is kept.

**Links.** The voice's page shows the link to each video under its name
("▶ \<channel\> on YouTube: \<title\>"), and each recording shows its own. A recording
uploaded as a file can be given the link it came from: "Add its YouTube link"
looks the video up for its title and channel without downloading it.

**When links stop working.** YouTube changes often, and an old yt-dlp stops
working. Update it with `python -m pip install -U "yt-dlp[default]"` in
Echo's environment. YouTube also needs a JavaScript runtime for yt-dlp: Node,
Deno, Bun or QuickJS, whichever is installed.

### More than one speaker

Videos often have more than one voice: an interviewer, a second character, a
narrator. Each line of 1.5 s or more gets a speaker embedding, and the lines
are grouped by how alike they are. Speaker 1 is the one with the most speech,
and a new voice is speaker 1.

When a recording has more than one speaker, the voice's page shows **Who's
speaking**: each speaker's lines, typical pitch and a sample to play.

- **Use this one** makes the voice that speaker instead. Lines Echo picked
  from the old speaker that you haven't practised (in a take or in the
  trainer) are swapped for the new speaker's.
- **Make a separate voice** gives that speaker a voice of their own, with
  their best lines picked.

### Tagged lines

In a recording's list each line has a play button and **Practise**, which
opens it in the trim view with 1.5 s either side so its ends can be adjusted.
Lines that make poor targets are tagged and hidden until you untick the box:

- **whispered:** under 20% of the words' frames have a pitch. Pitch, melody
  and resonance can't be compared without one, so those parts show "–".
- **other voice:** the line doesn't sound like the recording's main voice.
  This catches the other speakers in a video, or a song in the background.
- **unclear:** Whisper's mean word confidence is under 0.5. Check the line's
  words after adding it.

### Suggested lines

The voice's page lists the best lines from its recordings that it doesn't
have yet, each with ▶ and a one-click **Add**. **Auto-pick** tops the voice up
to five. A suggested line:

- is untagged and has at least three words;
- is near 3.5 s long, long enough to have a tune and short enough to hold in
  your head;
- is clearly voiced and sure of its words;
- is most like the main voice;
- is a whole sentence;
- sits within 4 semitones of the voice's median pitch. A line far outside it
  is usually creaky voice or a pitch tracker off by an octave.

A line is read again by itself when it's added. If its first word isn't the
one the transcript gave it, the transcript missed the start of the sentence;
the cut is then widened back by up to 0.8 s, never into the line before.
Auto-pick leaves out a line that still starts mid-sentence. "Pick by hand"
opens the recording's whole list.

## The microphone

The sidebar's **Microphone** box lists the microphones by name once the
browser has allowed one. Choose yours there: Echo remembers it and asks the
browser for exactly that one from then on. **Test** shows its level for a few
seconds without recording anything.

This matters on a computer with virtual audio devices, which streaming,
headset and VR software add. A browser told to remember the permission stops
asking which microphone and hands over the system's default, which is often
one of those devices, hearing nothing.

The microphone stays open between takes, so a browser that asks every time
it's opened (Firefox, unless you tick "Remember this decision") asks once. It
turns off after 15 minutes unused, or with **Turn off**. If nothing at all is
coming from it, the page says so while you record. If it's unplugged during a
take, the take ends and is scored with what was recorded.

## Practising a line

The practice page is built to work on the voice, not just test it, and to
say what to do next at every point.

- **Listen.** Play the target at full, ¾ or ½ speed (slowed, not lowered),
  your last take, or both back to back.
- **The note.** "♪ G3" plays a soft tone at the target's typical pitch to hum
  to. The guide shows it as a dashed line.
- **Warm up** records with the live guide on but saves nothing: use it to
  find the target's pitch before a scored take.
- **The guide.** A chart of the target's pitch through the line, with the
  words underneath and its usual range shaded. While you record, your pitch is
  drawn over it as you speak, lined up with where the target starts. After the
  take your whole contour sits on the target's timeline.
- **Record a take**, or press **Space**. The take stops by itself once you've
  said the line (below); Space or the button stops it sooner.
- **First take on a line:** a checklist. Listen two or three times, find the
  note (play it, then hum it with Warm up), then record.

**A take stops by itself** about a second after your last word. The page
listens to the microphone's level and waits for a quiet a little longer than
the longest pause between the line's own words (0.9 to 2 seconds). What counts
as speaking is judged against the room, 10 dB above its noise floor, so a soft
voice still counts and a fan doesn't. If you've said less than half the line
it waits longer (2.5 s), so a false start or a breath doesn't end the take. A
scored attempt in the trainer stops the same way. Warming up and "Try it live"
run until you stop them. Untick **Stop a take when I finish speaking** under
the microphone list to stop every take by hand.

### The result

- **The score** sits on a bar from a stranger (20) to the real person (90),
  with your best before it, and each part shows its change since the last take
  and a small trend.
- **The Voice part** also says, in words, what listeners would likely answer
  if asked whether the two recordings are the same speaker, for example
  "probably the same speaker (5.2 of 6)". **Passes a speaker check** shows
  when a speaker-verification model would accept the take as the same person;
  that threshold lets 1% of same-sex strangers through and accepts 88% of the
  real person's own takes.
- **Resonance** is a rough reading on one short take. From the third take on a
  line, its row also says where your last three takes sit together, which is
  steadier.
- **The words.** Each take is transcribed and compared with the line. When a
  take skipped, swapped or added words, the first hint says which. The word
  check doesn't change the score, because the recogniser sometimes mishears a
  correct take. If it misheard the target, correct the line with **Edit**.
- **Word by word.** Each phrase is coloured green, amber or red by how close it
  came, on the part the coach is working on. Click one to hear the target say
  it, then you, and to read what's off, for example "3.1 semitones lower than
  the target; darker (formants 9% lower)". The per-phrase numbers are a guide
  to where to listen, not scores: a single word is noisier than a whole line.

**Words said in one breath** stay together. In "The proper course to pursue
is to offer your name and address", "to pursue is to" comes out as one
stretch of sound, and a word that can't be heard on its own can't be practised
or measured on its own. Adjacent words are joined into one phrase when the
sound doesn't dip between them by 6 dB or more.

### The coach

After each take the coach names one part to work on next, with advice, a short
drill and the words furthest off. It picks the part that would lift the score
most: the part's weight times its distance from 100. It stays on that part
until the part reaches 80, unless another becomes clearly bigger, and then says
how it moved, for example "resonance: 48 → 66, closer". Voice is never the
focus, because it follows from the others. You can pick the focus yourself; it
stays until you switch back to Auto.

- **Drills.** Pitch: play the note, warm up on it. Melody: hum the tune at ½
  speed, then speak along. Resonance and articulation: play the phrase
  furthest off and copy it. Rhythm: speak along. **Speak along** plays the
  target with the live guide on, so you can match it as it goes; use
  headphones.
- **A part that isn't moving.** If a part has stayed under 15 for three takes
  in a row, the coach works on the parts that do respond, and says so (unless
  you picked that part yourself).
- **When to move on.** If four takes in a row land within 6 points and none
  beats your best, the coach suggests a fresh line. At 85 it calls the line
  done.

### Live pitch and resonance

While you record or warm up, a chart scrolls your pitch over the last few
seconds against the target's pitch band, with a readout such as "You: 170 Hz
(F3) · target about 202 Hz (G♯3) — go higher (3.0 st)". Under it a meter runs
from darker to brighter with the target in the middle: "Resonance so far:
about 6% brighter than the target". It lines up what you've said since your
last pause with the target sound by sound, the way a take's Resonance is
measured. Both are measured in the browser; the scores still come from the
server once the take is in. With the microphone open and no one speaking, a
steady hum can show as a pitch.

## Training one part

Each part in a take's result has **Train ›**, which opens a phrase-by-phrase
drill for that part on that line. You hear the target say one phrase, say
just that, and get its score in about a second. The phrases come furthest-off
first, and a phrase counts once three attempts in a row score 80 or more.
Every attempt is saved with its audio, so training picks up where it left
off; **▶ You** replays your last attempt.

- **Resonance** and **pitch** show where the attempt landed on a meter (darker
  to brighter, ±25%; lower to higher, ±6 semitones), with the band where a
  phrase scores 90 or more.
- **Both together** places every attempt on a map of pitch across and
  resonance up and down. Going lower tends to darken a voice and going higher
  to brighten it, so this is where to practise moving one while holding the
  other. The tips follow the combination: to go lower but stay as bright, keep
  the tongue forward and don't drop the jaw or larynx.
- **"♪ Its note"** plays the pitch the target uses on that phrase.
- **Live.** While the microphone is open a ring on the map follows your voice:
  where the last 1.2 seconds of it sit against the target's phrase, with a
  short tail showing which way you're moving. **Try it live** opens the
  microphone for up to 45 seconds without scoring or keeping anything: hold a
  sound, slide the pitch, and watch whether the brightness comes with it.
- **Melody and rhythm** run across the whole line, so their Train sets the
  coach's focus and points at the line drills.

## The voice check

One line can be luck: a take that happens to suit that sentence. A **voice
check** walks through the voice's ticked lines (three or more; five by
default) on the practice page. You can listen to each line as often as you
like, but you get one take and no retries, like a performance. The coach,
earlier takes and the score stay hidden until the end. The result shows:

- the **voice score**, the plain mean of the lines' scores;
- each line's score, with the weakest marked;
- each part averaged over the lines;
- whether the lines were **steady** (within 10 points of each other) or
  **uneven**;
- the habits that showed up in every line: the median pitch, brightness and
  pace gaps;
- the part that would lift the voice most.

Every line is on the same calibrated scale, so the mean keeps its meaning.
Keep the same lines ticked between checks, and the checks compare like with
like.

## Reads beside the score

**Gender read.** Every take has a row that reads 100 when the take sounds as
female or male as the target does, with both reads in the detail, each from 0
(male) to 100 (female). It comes from pitch and the upper formants, which carry
most of perceived voice gender. It isn't counted in the score, which already
has pitch and resonance in it, and its Train button opens "pitch and
resonance together", since those are what move it. The voice's page shows the
voice's own read.

**Vocal fry.** How much of the target's voiced sound drops into a low creak
(more than 7 semitones under the voice's usual pitch), and how much of yours.
A line that is mostly fry reads an octave low, and matching its pitch gets
nowhere until you creak where the target does. Ordinary speech reads 2–13%, so
only a gap against the target means anything. It isn't counted in the score.

## Lines that can't be scored fairly

A line whose pitch sits more than 7 semitones from its voice's usual pitch
gets a warning, and Pitch and Melody aren't scored on it. Such a line is
usually creaky voice, or a pitch tracker off by an octave, and its pitch
scores would say nothing about you: on one such line, read at 98 Hz against
the voice's usual 191, every take scored Pitch 2 and was told to go lower. The
line's headline comes from the other parts, and the voice check leaves it out
unless you tick it.

## Where things are kept

Voices, lines, takes, trainer attempts and voice checks are kept in `data/`
(or wherever `--data` or `ECHO_DATA` points). Its files are written whole or
not at all, so a crash can't leave half a file, and a file that can't be read
is left out of its list while the rest still open.

Every target clip goes through vocal separation before anything is measured,
because clips from films and videos usually have music or background under
the voice. The upload is kept as `original.wav` next to the cleaned
`target.wav`. Your takes aren't cleaned: they come from your own microphone,
and changing your voice before scoring it would defeat the point.

**Scores from an earlier version** are worked out again. Each result is
stamped with the scoring it came from. When that changes, Echo scores every
saved take again from its own audio the next time it starts, in the
background, so a best or a trend never mixes two scales.
