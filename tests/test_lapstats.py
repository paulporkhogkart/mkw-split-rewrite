from mkw_tracker.race.lapstats import LapStatsTracker


def test_coins_are_signed_deltas_between_lap_lines():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=5)            # 5 - 0
    ls.record_lap(2, coin_count=3)            # 3 - 5 = -2 (negative is valid)
    assert ls.per_lap[1]["coins"] == 5
    assert ls.per_lap[2]["coins"] == -2


def test_mushrooms_used_counts_decrements_within_a_lap():
    ls = LapStatsTracker()
    ls.update(3); ls.update(2); ls.update(1)  # lap 1: two uses
    ls.record_lap(1, coin_count=0)
    ls.update(1)                              # lap 2: none
    ls.record_lap(2, coin_count=0)
    ls.update(2); ls.update(1)               # lap 3: a pickup (gain, ignored) then one use
    ls.record_lap(3, coin_count=0)
    assert [ls.per_lap[i]["shrooms"] for i in (1, 2, 3)] == [2, 0, 1]


def test_unread_coins_are_none_and_do_not_move_the_baseline():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=None)
    ls.record_lap(2, coin_count=4)           # baseline still 0 -> 4
    assert ls.per_lap[1]["coins"] is None
    assert ls.per_lap[2]["coins"] == 4


def test_record_lap_is_idempotent_per_lap():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=5)
    ls.record_lap(1, coin_count=9)           # second call for lap 1 is ignored
    assert ls.per_lap[1]["coins"] == 5


def test_reset_clears_everything():
    ls = LapStatsTracker()
    ls.update(2); ls.record_lap(1, coin_count=7)
    ls.reset()
    assert ls.per_lap == {}
    ls.record_lap(1, coin_count=3)
    assert ls.per_lap[1]["coins"] == 3       # baseline reset to 0
