# The 48 orientations, explained simply

## 1. What the sensor actually reports

The accelerometer in the watch gives **three numbers** every 1/50 of a second: how hard it is being pushed along each of its own three directions, called **x**, **y** and **z**.

When the watch is still, the only push is **gravity** (1 g, pointing down). So the three numbers tell you **which way the sensor is facing**:

```
watch lying face up on a table        watch hanging on your arm, hand down
        z = +1                                 x = -1
        x =  0                                 y =  0
        y =  0                                 z =  0
```

(The exact numbers depend on the device, which is the whole point of this document.)

## 2. The problem: every device names its directions differently

The models learned from **Capture-24's sensor**. On that sensor, "x" points one particular way along the wrist, "y" another, "z" another.

The SilverGuard watch's chip (MPU-6050) is a different device, soldered into a different case. **Its x might point where Capture-24's z pointed.** The model doesn't know that. It just sees "x" and assumes it means what x meant during training.

**An everyday comparison:** you learned to drive in a car where the indicator is on the **left** of the steering wheel. Now you get a car where it's on the **right**. Your habits are perfect, but you'd keep switching on the wipers. Nothing is wrong with you or the car; the controls are just **labelled differently**. The fix is to remember "this car's right stalk = my old left stalk".

That's what the alignment does: it works out **"watch x = training z"** (and so on), and translates every reading before the model sees it.

## 3. Where the number 48 comes from

The chip is mounted **square** in the watch (not tilted at some odd angle), so its axes can only differ from Capture-24's in two simple ways.

### Way 1: the axes can be in a different order (6 possibilities)

Which watch axis plays the part of the model's x, y and z?

| # | model x is | model y is | model z is |
|---|---|---|---|
| 1 | watch x | watch y | watch z |
| 2 | watch x | watch z | watch y |
| 3 | watch y | watch x | watch z |
| 4 | watch y | watch z | watch x |
| 5 | watch z | watch x | watch y |
| 6 | watch z | watch y | watch x |

That's every way to arrange three things in order: 3 × 2 × 1 = **6**.

### Way 2: each axis can point the opposite way (8 possibilities)

Even with the right order, an axis can be **reversed**: the watch says +0.8 where Capture-24 would have said −0.8 (the chip is flipped over). Each of the three axes is either normal (+) or reversed (−):

```
+ + +    + + -    + - +    + - -
- + +    - + -    - - +    - - -
```

That's 2 × 2 × 2 = **8**.

### Together: 6 × 8 = 48

Every possible setup is one order **and** one sign pattern, so there are **6 × 8 = 48** possibilities. Exactly one of them (or a few that work equally well) is right for our watch.

We write each one like this:

> **model x, y, z = watch −y, +x, +z**
>
> means: the model's x = the watch's y, reversed. The model's y = the watch's x. The model's z = the watch's z.

The "do nothing" one is **model x, y, z = watch +x, +y, +z**.

## 4. Two kinds among the 48: turns and mirror images

- **24 are real turns.** You could get them by physically rotating the chip (for example, a quarter-turn on the board).
- **24 are mirror images**, like your reflection, where left and right swap. A chip can't be mirrored by turning it, but you get the same effect by **wearing the watch on the other wrist**: your left arm moves like a mirror image of your right arm.

So the 48 cover both "how the chip sits in the case" and "which wrist it's on".

## 5. Why it matters (real numbers from Stage 7)

Best model (XGBoost + HMM) on 30 Capture-24 test people. 1 = perfect, 0 = guessing:

| Setup | 4 activities | Still vs moving |
|---|---|---|
| **Right** | **0.818** | **0.790** |
| x and z swapped | 0.787 | 0.739 |
| y and z swapped | 0.719 | 0.664 |
| Worst of the 48 | 0.665 | 0.590 |

And on GOTOV (older people, a different brand of sensor), fixing the setup took still-vs-moving from **0.895 to 0.990**.

## 6. How we find the right one without anyone labelling anything

Over a normal day, a wrist spends a lot of time in typical positions: flat on a desk, hanging while walking, resting on a pillow. In Capture-24 we know **where gravity points** in each of those situations.

The tool:

1. Takes a few hours of **normal watch wear** (no labels, no script)
2. Groups the 30-second windows by **how much the wrist is moving** (still, a bit, a lot). This grouping doesn't depend on the axes, so it works on any device
3. **Tries all 48 setups** on the watch data, one at a time
4. For each, compares "where gravity points" with Capture-24's pattern
5. **Picks the setup that looks most like Capture-24**

It's like trying all 48 ways to hold a map until the roads line up with what you see out of the window.

## 7. Two shortcuts that make it reliable

- **Face-up check (10 seconds):** lay the watch face up. Whichever axis reads about ±1 g goes **through the face**. On most sensor boards that's z, on both devices, so only **16** of the 48 remain to choose from.
- **Pool several people:** the chip is mounted the same way in every watch of the same design, so recordings from 3–5 people can be combined. With 3 hours each from 5 people plus the face-up check, the tool never lost more than 0.04 kappa in 200 tests.

## 8. What ends up in the firmware

Just two small tables: **which watch axis** feeds each model axis, and **its sign**:

```c
const uint8_t MODEL_FROM[3] = {1, 0, 2};    // model x = watch y, model y = watch x, model z = watch z
const int8_t  MODEL_SIGN[3] = {-1, +1, +1}; // model x is reversed
```

Every sample is translated with these before the model sees it. The **gyroscope** uses the same tables (plus one extra sign flip if the setup is a mirror image, that is, the other wrist).

## 9. Calibration is a separate fix

Lining up the axes says **which direction is which**. Calibration fixes **how big** the readings are: this watch reads **1.23 g** lying still instead of 1.00 g. That's handled by a 2-minute routine (10 seconds still in each of 6 positions: face up, face down, and on each edge). Details are in `stage7_axis_alignment.ipynb`, section 4.
