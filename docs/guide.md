# Echo user guide

This guide describes each screen of Echo and what it shows. The workflow has
four stages: create a **voice** from a video link or an uploaded recording;
let Echo pick the voice's lines, or pick them by hand; record takes on one line
at a time and follow the coach's advice; and take a **voice check** across
several lines to score the voice as a whole. Installation is described in the
[README](../README.md). How each measurement works, and how it was checked, is
described in the [method](method.md).

## Terms used in this guide

| Term | Meaning |
| --- | --- |
| Voice | The person or character being imitated. A voice holds the lines being practised and the recordings they came from. |
| Recording | A source recording of a voice, such as a long video of one person talking or a compilation of a game character's voice lines. |
| Line | A short clip of the voice, usually one sentence, cut from a recording or added on its own. The line's clip is the **target**. |
| Take | One recording of you saying a line, scored against the target. |
| Part | One of the six measures that make up the score (see [The six parts of the score](#the-six-parts-of-the-score)). |
| Pitch | How high or low a voice sounds: the rate at which the vocal folds vibrate, in hertz (Hz). Echo also gives the nearest musical note in scientific pitch notation, for example F3 (the F below middle C). |
| Semitone (st) | One step of the musical scale. Twelve semitones make an octave, a doubling of pitch. |
| Voiced | Sound made with the vocal folds vibrating, which therefore has a pitch. Whispered sound is not voiced. |
| Formant | A resonance of the throat and mouth, which strengthens a band of frequencies. F1, F2 and F3 are the three lowest formants; they are unrelated to the note names. Lower formants make a voice sound darker, higher formants brighter. |
| Vocal fry | A low, creaking voice quality, also called creaky voice (see [Vocal fry](#vocal-fry)). |
| Speaker embedding | A list of numbers that a speaker-recognition model computes from a recording. Recordings of the same person give similar embeddings. |

## The six parts of the score

A take's score combines six parts, each weighted by how well it tells the real
person from a stranger. The weights and the measurement behind each part are
given in the [method](method.md).

| Part | What it compares between the take and the target |
| --- | --- |
| Voice | The rating listeners would likely give the pair, on whether they are the same speaker. |
| Pitch | Median pitch, in semitones. |
| Melody | The shape of the pitch contour through the line, once the difference in level is removed. |
| Resonance | Formants F2 and F3: whether the take sounds brighter or darker than the target. |
| Articulation | How the speech sounds are shaped, compared through what a speech recogniser hears in each recording. |
| Rhythm | Pace (the length from the first to the last voiced sound) and timing within the line. |

## Voices and the voice page

Everything in Echo belongs to a voice. The sidebar lists the voices, and
nothing else. A voice's page has three tabs:

| Tab | Contents |
| --- | --- |
| **Practise** | The voice score, your lines, and a summary of the voice. |
| **Progress** | Every take as a dot (one colour per line), the average of the last five takes, and each part over your last five takes against your first five. |
| **Add lines** | Suggested lines, links, uploads, recordings and speakers. A voice with no lines yet opens on this tab. |

### The voice at a glance

Each recording summarises the voice from its lines: its typical pitch and
note, its usual range, its pace in words a second, and how breathy it is.
Breathiness is described from the share of the words' frames (short stretches
of audio analysed one at a time) that are voiced. Read speech is voiced on
53–60% of its word frames; a soft, breathy voice on about 45%. The read-speech
figure was measured on CMU ARCTIC, a corpus of read speech (Kominek and Black,
2004; see `summary` in `src/echo_voice/sources.py`).

### Your habits against the voice

Once you have practised a few of the voice's lines, the same card shows your
habits across all of them, from your last 10 takes, for example "3.0 semitones
low (you average 170 Hz, F3), darker (formants 11% lower)". Habits that appear
in every line are the ones to correct first.

## Getting lines from a video or recording

### Starting a voice from a link

1. Paste a link into the box on the first screen, at the top of the home page,
   or into **+ New voice**, which accepts a name or a link.
2. Echo creates a voice for the link immediately and opens the voice's page.
   A progress card on that page shows each processing step.

A link pasted on a voice's own page adds the video to that voice. An uploaded
file over a minute long goes through the same steps; a shorter file becomes
one line.

### Processing steps

A background job processes the recording in six steps:

1. **Download:** links are downloaded with yt-dlp, which supports YouTube and
   most video sites. Live streams are refused, because they have no end to
   wait for.
2. **Read:** the audio is decoded to mono at a 16 kHz sample rate.
3. **Transcribe:** voice-activity detection removes stretches with no speech,
   and Whisper base.en, an English speech-recognition model, transcribes the
   rest with a timestamp for each word.
4. **Split:** lines end at full stops and at pauses of 0.7 s or more. Lines
   over 10 s are split at a comma or pause near the middle.
5. **Speakers:** the lines are grouped by speaker (see
   [Recordings with more than one speaker](#recordings-with-more-than-one-speaker)).
6. **Pick:** the voice receives the five best lines of its speaker, enough for
   a voice check.

One 25-minute video took 75 seconds from pasting the link to five picked
lines. The job runs at below-normal priority, continues if the page is closed,
and starts again if the computer stops part-way through.

### How the voice is named

Once the video is known, the voice is named after its **channel**, which for
most creators is the person. A second video from a channel that already has a
voice joins that voice, with a few of its own lines picked, instead of creating
a second voice with the same name. When the site gives no channel, the voice is
named after the video's title, without [tags], (asides) or anything after
" | " or " - ".

The page offers the other name once: the title for a channel-named voice, or
the channel for a title-named one. For a fan's compilation of a character, for
example, the title "Ellie - All Voice Lines | The Last of Us Part II" gives
"Ellie". A name you type yourself is kept.

### Links to the source videos

The voice's page shows the link to each video under the voice's name
("▶ \<channel\> on YouTube: \<title\>"), and each recording shows its own link.
A recording uploaded as a file can be given the link it came from:
**Add its YouTube link** looks the video up for its title and channel without
downloading it.

### When links stop working

YouTube changes often, and an outdated yt-dlp stops working. Update it in
Echo's environment with:

```bash
python -m pip install -U "yt-dlp[default]"
```

For YouTube, yt-dlp also needs a JavaScript runtime; it uses whichever of
Node, Deno, Bun or QuickJS is installed.

### Recordings with more than one speaker

Videos often contain more than one voice: an interviewer, a second character,
a narrator. Each line of 1.5 s or more receives a speaker embedding, and the
lines are grouped by how similar their embeddings are. Speaker 1 is the
speaker with the most speech, and a new voice is speaker 1. How accurately
lines are grouped is measured in the [method](method.md).

When a recording has more than one speaker, the voice's page shows
**Who's speaking**: each speaker's lines, typical pitch and a sample to play.
Two actions are available for each speaker:

- **Use this one** makes that speaker the voice. Lines that Echo picked from
  the previous speaker and that you have not practised (in a take or in the
  trainer) are replaced with the new speaker's.
- **Make a separate voice** gives that speaker a voice of their own, with
  their best lines picked.

### Tagged lines

In a recording's list each line has a play button and **Practise**, which
opens the line in the trim view with 1.5 s either side, so that its ends can
be adjusted. Lines that make poor targets are tagged, and are hidden while
**Hide whispered, unclear and other voices** is ticked.

| Tag | Condition | Consequence |
| --- | --- | --- |
| whispered | Under 20% of the words' frames have a pitch. | Pitch, melody and resonance cannot be compared without a pitch, so those parts show "–". |
| other voice | The line does not sound like the recording's main voice. | This catches the other speakers in a video, or a song in the background. |
| unclear | Whisper's mean word confidence is under 0.5. | Check the line's words after adding it. |

### Suggested lines

The voice's page lists the best lines from its recordings that the voice does
not have yet, each with ▶ and a one-click **Add**. **Auto-pick** adds
suggested lines until the voice has five. A suggested line:

- is untagged and has at least three words;
- is near 3.5 s long: long enough to have a tune, and short enough to hold in
  memory;
- is clearly voiced, with a confident transcript;
- sounds most like the main voice;
- is a whole sentence;
- lies within 4 semitones of the voice's median pitch. A line far outside this
  range usually has an unreliable pitch (see
  [Lines that cannot be scored fairly](#lines-that-cannot-be-scored-fairly)).

When a line is added, it is transcribed again on its own. If its first word is
not the one the recording's transcript gave it, that transcript missed the
start of the sentence, and the cut is widened back by up to 0.8 s, never into
the line before. **Auto-pick** leaves out a line that still starts
mid-sentence. **Pick by hand** opens the recording's whole list.

### Cleaning of target clips

Every target clip goes through vocal separation before anything is measured,
because clips from films and videos usually have music or background sound
under the voice. The upload is kept as `original.wav` next to the cleaned
`target.wav`. Your takes are not cleaned: they come from your own microphone,
and altering your voice before scoring it would defeat the purpose. The effect
of separation on the scores is measured in
[Clip cleaning](method.md#clip-cleaning).

## Choosing the microphone

1. Allow the browser to use a microphone. The sidebar's **Microphone** box
   then lists the microphones by name.
2. Choose your microphone in that box. Echo remembers it and, from then on,
   asks the browser for exactly that microphone.
3. Press **Test** to show its level for a few seconds without recording
   anything.

This choice matters on a computer with virtual audio devices, which
streaming, headset and virtual-reality (VR) software add. A browser told to
remember the permission stops asking which microphone to use and supplies the
system's default, which is often one of those virtual devices and hears
nothing.

The microphone stays open between takes, so a browser that asks for
permission every time the microphone is opened (Firefox, unless "Remember this
decision" is ticked) asks only once. The microphone turns off after
15 minutes without use, or when **Turn off** is pressed. If no sound at all is
coming from it, the page says so during recording. If it is unplugged during a
take, the take ends and is scored with what was recorded.

## Practising a line

The practice page is designed to train the voice, not only to test it, and to
state the next step at every point. Its controls are:

| Control | What it does |
| --- | --- |
| Playback | Plays the target at full, ¾ or ½ speed (slowed, not lowered in pitch), your last take, or both one after the other. |
| The note ("♪ G3") | Plays a soft tone at the target's typical pitch, to hum to. The button is named after the note. The guide shows the note as a dashed line. |
| **Warm up** | Records with the live guide on but saves nothing. Use it to find the target's pitch before a scored take. |
| The guide | A chart of the target's pitch through the line, with the words underneath and the usual range shaded. While you record, your pitch is drawn over it as you speak, aligned with where the target starts. After the take, your whole contour is placed on the target's timeline. |
| **Record a take**, or **Space** | Records a scored take. The take stops by itself once you have said the line (see [When a take stops](#when-a-take-stops)); **Space** or the button stops it sooner. |

For the first take on a line, the page shows a checklist:

1. Listen to the target two or three times.
2. Find the note: play it, then hum it with **Warm up**.
3. Record a take.

### When a take stops

A take stops by itself about a second after your last word. The page monitors
the microphone's level and waits for a silence slightly longer than the
longest pause between the line's own words (0.9 to 2 seconds). Speech is
judged relative to the room: sound 10 dB (decibels) above the room's noise
floor counts as speech, so a soft voice still counts and a fan does not. If
you have said less than half the line, the page waits longer (2.5 s), so that
a false start or a breath does not end the take. A scored attempt in the
trainer stops in the same way. Warming up and **Try it live** run until you
stop them. To stop every take by hand, untick
**Stop a take when I finish speaking** under the microphone list. How the end
of a take was tested is described in the [method](method.md).

### The result of a take

- **The score** is shown on a bar from a stranger (20) to the real person
  (90), with your best score before it. Each part shows its change since the
  last take and a small trend. How the two ends of the scale were set is
  described in [Calibration](method.md#calibration).
- **The Voice part** also states in words what listeners would likely answer
  if asked whether the two recordings are the same speaker, for example
  "probably the same speaker (5.2 of 6)", on a scale from 1 (definitely
  different) to 6 (definitely the same). **Passes a speaker check** appears
  when a speaker-verification model, which decides whether two recordings are
  of the same person, would accept the take as the same person. Its threshold
  lets 1% of same-sex strangers through and accepts 88% of the real person's
  own takes, as measured in [Calibration](method.md#calibration).
- **Resonance** is a rough reading on one short take. From the third take on a
  line, its row also shows where your last three takes lie together, which is
  steadier. How much small changes to a take move Resonance is measured in the
  [method](method.md).
- **The words.** Each take is transcribed and compared with the line. When a
  take skipped, swapped or added words, the first hint says which. The word
  check does not change the score, because the recogniser sometimes mishears a
  correct take; its false-alarm rate is measured in the [method](method.md).
  If the recogniser misheard the target, correct the line with **Edit**.
- **Word by word.** Each phrase is coloured green, amber or red by how close it
  came, on the part the coach is working on. Click a phrase to hear the target
  say it, then you, and to read what differs, for example "3.1 semitones lower
  than the target; darker (formants 9% lower)". The per-phrase numbers show
  where to listen; they are not scores, because a single word is noisier than
  a whole line.

### Phrases: words said in one breath

Words said in one breath stay together as one phrase. In "The proper course to
pursue is to offer your name and address", "to pursue is to" is one continuous
stretch of sound, and a word that cannot be heard on its own cannot be
practised or measured on its own. Adjacent words are joined into one phrase
when the sound does not dip by 6 dB or more between them.

### The coach

After each take the coach names one part to work on next, with advice, a short
drill and the words furthest off. It picks the part that would raise the score
most: the part's weight multiplied by its distance from 100. It stays on that
part until the part reaches 80, unless the gain from another part becomes
clearly larger, and then reports how the part moved, for example "resonance: 48 → 66, closer".
Voice is never the focus, because it follows from the other parts. You can
choose the focus yourself; it then stays until you switch back to **Auto**.

The drills for each part are:

| Part | Drill |
| --- | --- |
| Pitch | Play the note, then warm up on it. |
| Melody | Hum the tune at ½ speed, then speak along. |
| Resonance and articulation | Play the phrase furthest off and copy it. |
| Rhythm | Speak along. |

**Speak along** plays the target with the live guide on, so that you can match
it as it plays. Use headphones.

The coach also follows two rules:

- **A part that is not improving.** If a part has stayed under 15 for three
  takes in a row, the coach works on the parts that do respond, and says so,
  unless you chose that part yourself.
- **When to move on.** If four takes in a row land within 6 points of each
  other and none beats your best, the coach suggests a different line. At 85
  it calls the line done.

### Live pitch and resonance

While you record or warm up, a chart scrolls your pitch over the last few
seconds against the target's pitch band, with a readout such as "You: 170 Hz
(F3) · target about 202 Hz (G♯3) — go higher (3.0 st)". Below it, a meter runs
from darker to brighter with the target in the middle: "Resonance so far:
about 6% brighter than the target". The meter aligns what you have said since
your last pause with the target, sound by sound, in the same way as a take's
Resonance is measured. Both readings are computed in the browser; the scores
still come from the server once the take is complete. With the microphone open
and no one speaking, a steady hum can appear as a pitch. How closely the live
meter agrees with the scorer is measured in
[Live resonance meter](method.md#live-resonance-meter).

## Training one part

Each part in a take's result has **Train ›**, which opens the trainer: a
phrase-by-phrase drill for that part on that line. You hear the target say one
phrase, say only that phrase, and receive its score in about a second. The
phrases come furthest off first, and a phrase counts as done once three
attempts in a row score 80 or more. Every attempt is saved with its audio, so
the trainer resumes where it stopped; **▶ You** replays your last attempt.

| Part | What the trainer shows |
| --- | --- |
| Resonance | Where the attempt landed on a meter from darker to brighter (±25%), with the band where a phrase scores 90 or more. |
| Pitch | Where the attempt landed on a meter from lower to higher (±6 semitones), with the band where a phrase scores 90 or more. |
| **Both together** | Every attempt on a map, with pitch across and resonance up and down (see below). |
| Melody and rhythm | These parts run across the whole line, so their **Train ›** sets the coach's focus and points to the line drills. |

**Both together.** Going lower tends to darken a voice, and going higher tends
to brighten it, so this map is where to practise moving one while holding the
other. The tips follow the combination: to go lower but stay as bright, keep
the tongue forward and do not drop the jaw or larynx.

**♪ Its note** plays the pitch the target uses on that phrase.

**Live feedback.** While the microphone is open, a ring on the map follows
your voice: where the last 1.2 seconds of it lie against the target's phrase,
with a short tail showing which way you are moving. **Try it live** opens the
microphone for up to 45 seconds without scoring or keeping anything. Use it to
hold a sound, slide the pitch, and watch whether the brightness follows.

## The voice check

A single line can give a lucky result: a take that happens to suit that
sentence. A voice check therefore goes through the voice's ticked lines
(three or more; five by default) on the practice page:

1. Each line is presented in turn. Listen to it as often as you like.
2. Record one take of the line. There are no retries, as in a performance.
3. After the last line, the result is shown. The coach, earlier takes and the
   score stay hidden until then.

The result shows:

- the **voice score**, the plain mean of the lines' scores;
- each line's score, with the weakest marked;
- each part averaged over the lines;
- whether the lines were **steady** (within 10 points of each other) or
  **uneven**;
- the habits that appeared in every line: the median gaps in pitch, brightness
  and pace;
- the part that would raise the voice score most.

Every line is on the same calibrated scale, so the mean keeps its meaning.
Keep the same lines ticked between checks, so that the checks compare like
with like.

## Reads beside the score

Two further reads are shown beside the parts. Neither is counted in the score.

### Gender read

Every take has a row that reads 100 when the take sounds as female or as male
as the target does. The detail shows both reads, each from 0 (male) to 100
(female). The read is computed from median pitch and the shift of the second
and third formants (F2 and F3). The score already includes pitch and resonance, so
the gender read is not added to it. Its **Train ›** button opens "pitch and
resonance together", since those are what move it. The voice's page shows the
voice's own read. The model behind the read, and its accuracy, are described
in [Gender read](method.md#gender-read).

### Vocal fry

The vocal-fry read is the share of the target's voiced sound that drops into a
low creak (more than 7 semitones under the voice's usual pitch), and the same
share for your take. A line that is mostly fry reads an octave low, and
matching its pitch achieves nothing until you creak where the target does.
Ordinary speech reads 2–13%, so only a gap against the target is meaningful.
How the read was checked is described in [Vocal fry](method.md#vocal-fry).

## Lines that cannot be scored fairly

A line whose pitch lies more than 7 semitones from its voice's usual pitch
receives a warning, and Pitch and Melody are not scored on it. Such a line is
usually creaky voice, or a pitch tracker off by an octave, and its pitch
scores would say nothing about your take: on one such line, read at 98 Hz
against the voice's usual 191 Hz, every take scored Pitch 2 and was told to go
lower. The line's headline score comes from the other parts, and the voice
check leaves the line out unless you tick it.

## Where data is kept

Voices, lines, takes, trainer attempts and voice checks are kept in `data/`
(or wherever `--data` or `ECHO_DATA` points). Each file is written whole or
not at all, so a crash cannot leave half a file, and a file that cannot be
read is left out of its list while the rest still open.

**Scores from an earlier version** are recalculated. Each result is stamped
with the version of the scoring that produced it. When the scoring changes,
Echo scores every saved take again from its own audio the next time it starts,
in the background, so a best score or a trend never mixes two scales.
