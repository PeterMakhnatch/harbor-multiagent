import random
import unittest

from flotilla_analysis import stats


class TQuantile(unittest.TestCase):
    def test_matches_tables(self):
        # two-sided 95% critical values of Student's t
        for df, expected in [
            (1, 12.7062047),
            (2, 4.3026527),
            (5, 2.5705818),
            (11, 2.2009852),
            (30, 2.0422725),
            (1000, 1.9623391),
        ]:
            self.assertAlmostEqual(stats.t_quantile(0.975, df), expected, places=6)

    def test_cdf_is_symmetric(self):
        self.assertAlmostEqual(stats.t_cdf(0.0, 4), 0.5)
        self.assertAlmostEqual(stats.t_cdf(-2.0, 7) + stats.t_cdf(2.0, 7), 1.0)


class TInterval(unittest.TestCase):
    def test_interval_over_instance_means(self):
        estimate = stats.t_interval([0.0, 1.0, 1.0, 0.0, 1.0])
        half = stats.t_quantile(0.975, 4) * stats.sample_sd([0.0, 1.0, 1.0, 0.0, 1.0]) / 5**0.5
        if estimate.value is None or estimate.low is None or estimate.high is None:
            self.fail("five values give an interval")
        self.assertAlmostEqual(estimate.value, 0.6)
        self.assertAlmostEqual(estimate.low, 0.6 - half)
        self.assertAlmostEqual(estimate.high, 0.6 + half)

    def test_clipping_and_notes(self):
        clipped = stats.t_interval([0.0, 1.0, 1.0, 0.0, 1.0], bounds=(0.0, 1.0))
        self.assertEqual((clipped.low, clipped.high), (0.0, 1.0))
        flat = stats.t_interval([1.0, 1.0, 1.0])
        self.assertEqual((flat.low, flat.high), (1.0, 1.0))
        self.assertIn("zero-width", flat.note)
        single = stats.t_interval([0.5])
        self.assertEqual((single.value, single.low, single.high), (0.5, None, None))
        self.assertEqual(stats.t_interval([]).value, None)


class SignFlip(unittest.TestCase):
    def test_exact(self):
        rng = random.Random(0)
        self.assertEqual(stats.sign_flip_test([1, 1, 1, 1, 1], rng).p_value, 2 / 32)
        self.assertEqual(
            stats.sign_flip_test([1, 1, 1, 1, 1, 0, 0], rng).p_value, 2 / 32
        )  # zeros cannot flip
        self.assertEqual(stats.sign_flip_test([1, -1], rng).p_value, 1.0)
        self.assertEqual(stats.sign_flip_test([0, 0], rng).p_value, 1.0)
        self.assertIsNone(stats.sign_flip_test([], rng).p_value)
        # |sum| = 1 for [1, 1, -1]: patterns with |sum| >= 1 are all 8
        self.assertEqual(stats.sign_flip_test([1, 1, -1], rng).p_value, 1.0)

    def test_monte_carlo_is_seeded(self):
        values = [1.0] * 17 + [0.5]
        first = stats.sign_flip_test(values, random.Random("s"), draws=2000)
        second = stats.sign_flip_test(values, random.Random("s"), draws=2000)
        self.assertEqual(first, second)
        self.assertEqual(first.method, "monte carlo")
        self.assertEqual(first.p_value, 1 / 2001)  # no random pattern is as extreme as all-positive


class Recovery(unittest.TestCase):
    def test_point_and_interval(self):
        result = stats.bootstrap_recovery([1, 1, 0, 1], [1, 1, 1, 1], random.Random(1), resamples=2000)
        self.assertEqual(result.value, 0.75)
        self.assertEqual(result.undefined_resamples, 0)
        if result.low is None or result.high is None:
            self.fail("no resample is undefined, so the interval exists")
        self.assertLessEqual(result.low, 0.75)
        self.assertGreaterEqual(result.high, 0.75)
        again = stats.bootstrap_recovery([1, 1, 0, 1], [1, 1, 1, 1], random.Random(1), resamples=2000)
        self.assertEqual(result, again)

    def test_undefined_rules(self):
        none = stats.bootstrap_recovery([0.0, 0.0], [0.0, -1.0], random.Random(1))
        self.assertIsNone(none.value)
        self.assertIn("no information gap", none.note)
        shaky = stats.bootstrap_recovery([1, 0, 0, 1], [1, 0, 0, 0], random.Random(1), resamples=2000)
        self.assertEqual(shaky.value, 2.0)
        self.assertIsNone(shaky.low)
        self.assertGreater(shaky.undefined_resamples, 0.025 * 2000)

    def test_percentile_type_7(self):
        self.assertEqual(stats.percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertAlmostEqual(stats.percentile([0.0, 10.0], 0.025), 0.25)
