# Flag rules — quick reference

Mirrors `engine/params.py` + `engine/classify.py`. Units: metres / km/h / minutes.

## Parameters

```
glitch_cap      = 150 km/h          # segments faster than this are dropped (data-locked)
v_move_min      = 2 km/h            # a segment must reach this to count as "moving"
gate            = 200 m             # gap <= this => contiguous, no flag
buffer          = 0.10 * t + 3 min  # buffer_frac = 0.10, D = 3 min
missing_band    = 20 min            # width of the missing-trip band above buffer
mode_low/high   = 12 / 25 km/h      # slow class <=12, fast class >=25, 12-25 = dead zone
zero_move       = disp < 200 m AND speed <= 10 km/h
```

## Speed (used everywhere: v_A, v_B, zero_move)

```
segs   = segment speeds of the GPS route                 # km/h
moving = [s for s in segs if 2 <= s <= 150]              # v_move_min .. glitch_cap
if moving:           speed = median(moving)
elif any s <= 150:   speed = 0.0                         # real segs, all sub-floor -> stationary
else:                speed = displacement / duration     # sparse / all-glitch -> fallback
```

## Per trip — zero_move

```
disp  = haversine(trip.start, trip.end)
speed = median_moving_speed(trip)

if disp < 200 and speed <= 10:   ZERO_MOVE
else:                            (no per-trip flag)
```

## Per consecutive pair A -> B  (same person-day, sorted by start time)

```
gap_dist = haversine(A.end, B.start)
gap_time = B.start - A.end
v_A, v_B = median_moving_speed(A), median_moving_speed(B)
t        = (gap_dist / 1000) / v_A * 60      # expected travel time at v_A
buffer   = 0.10 * t + 3
residual = gap_time - t                      # SIGNED

if gap_time <= 0:                              KEEP   # overlap / duplicate
elif gap_time < (gap_dist/1000)/120*60:        KEEP   # implies > 120 km/h -> glitch
elif v_A <= 0 or v_B <= 0:                     KEEP   # a leg's speed undefined / stationary
elif gap_dist <= 200:                          KEEP   # contiguous (chain intact)

elif residual < buffer:                               # travel-shaped at v_A
    if (v_A <= 12 and v_B >= 25) or (v_A >= 25 and v_B <= 12):
                                               MODE_CHANGE      # speed-class switch -> review, never merge
    else:                                      FRAGMENT         # same class -> merge

elif residual <= buffer + 20:                  MISSING_TRIP
else:                                          MISSING_ACTIVITY
```

## Manual-logged vs auto-detected trips

```
is_manual = purpose_of_travel is present     # app auto-detects every trip;
                                             # the user fills purpose only when they label it

# Verdict is IDENTICAL for manual and auto trips:
#   - classification uses MEASURED speed only, never the mode label
#   - every flag is emitted regardless of is_manual (incl. user-logged recreational walks)

# is_manual gates the ACTION, not the verdict (EXPLICIT-APPLY — nothing auto-applies):
if reviewer accepts flag:   apply change
else:                       keep trip unchanged
```
