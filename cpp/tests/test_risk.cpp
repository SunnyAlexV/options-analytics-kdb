#include <gtest/gtest.h>

#include <cmath>
#include <vector>

#include "oak/black76.hpp"
#include "oak/risk.hpp"

using namespace oak;

namespace {
// A BTC-like skewed smile: ~50% ATM, puts bid (rho < 0), 30 days.
Smile smile30() { return Smile{60000.0, 30.0 / 365, SVI{0.010, 0.06, -0.35, 0.02, 0.15}}; }
}  // namespace

TEST(Risk, StickyStrikeKeepsEachStrikesVol) {
    const Smile s = smile30();
    for (double K : {45000.0, 60000.0, 75000.0})
        EXPECT_NEAR(smile_vol(s, K, 0.05, 1.0), smile_vol(s, K, 0.0, 1.0), 1e-15);
}

TEST(Risk, StickyMoneynessMovesTheSmileWithTheForward) {
    const Smile s = smile30();
    const double x = 0.05, F1 = s.F * std::exp(x);
    for (double m : {0.8, 1.0, 1.2})   // same moneyness K/F before and after
        EXPECT_NEAR(smile_vol(s, m * F1, x, 0.0), smile_vol(s, m * s.F, 0.0, 0.0), 1e-15);
}

TEST(Risk, AtmVolMovesBySkewTimesRTimesMove) {
    // d sigma_ATM = R * skew * x to first order (the definition of the skew-stickiness ratio)
    const Smile s = smile30();
    const double x = 1e-5, skew = smile_slope(s, s.F);
    for (double R : {0.0, 0.5, 1.0, 1.5}) {
        // central difference: ATM after +x and after -x (a one-sided one is off by sigma'' R^2 x / 2)
        const double up = smile_vol(s, s.F * std::exp(x), x, R);
        const double dn = smile_vol(s, s.F * std::exp(-x), -x, R);
        EXPECT_NEAR((up - dn) / (2 * x), R * skew, 1e-8);
    }
}

TEST(Risk, BaseScenarioIsSumOfBlackPrices) {
    const std::vector<Smile> sm{smile30()};
    const std::vector<Position> book{{2.0, 55000, 0, false, false}, {-1.0, 65000, 0, true, false}};
    double expect = 0;
    for (const auto& p : book)
        expect += p.qty * price(sm[0].F, p.K, sm[0].T, smile_vol(sm[0], p.K, 0, 1), 0, p.call);
    EXPECT_NEAR(book_values(book, sm, {Scenario{}}, 1.0)[0], expect, 1e-9);
}

TEST(Risk, ParityBookHasNoRiskInAnyScenario) {
    // long call, short put, short a future entered at K: C - P - (F - K) = 0 for any F, vol, time
    const std::vector<Smile> sm{smile30()};
    const double K = 62000;
    const std::vector<Position> book{{1, K, 0, true, false}, {-1, K, 0, false, false}, {-1, K, 0, true, true}};
    std::vector<Scenario> sc;
    for (double x : {-0.3, 0.0, 0.2})
        for (double dv : {-0.1, 0.0, 0.15})
            for (double dt : {0.0, 10.0 / 365, 40.0 / 365}) sc.push_back({x, dv, 1.0, dt});
    for (double R : {0.0, 1.0})
        for (double v : book_values(book, sm, sc, R)) EXPECT_NEAR(v, 0.0, 1e-7);
}

TEST(Risk, RuleDeltaMatchesFiniteDifference) {
    // Delta_R = Delta_BS + vega (R - 1) sigma'(k) / F
    const Smile s = smile30();
    const std::vector<Smile> sm{s};
    for (double K : {50000.0, 60000.0, 70000.0})
        for (double R : {0.0, 1.0, 0.6}) {
            const std::vector<Position> book{{1, K, 0, K >= s.F, false}};
            const double h = 1e-5;
            auto v = book_values(book, sm, {{h, 0, 1, 0}, {-h, 0, 1, 0}}, R);
            const double fd = (v[0] - v[1]) / (s.F * (std::exp(h) - std::exp(-h)));
            const double vol = smile_vol(s, K, 0, R);
            const Greeks g = greeks(s.F, K, s.T, vol, 0, K >= s.F);
            const double analytic = g.delta + g.vega * (R - 1.0) * smile_slope(s, K) / s.F;
            EXPECT_NEAR(fd, analytic, 1e-6) << "K=" << K << " R=" << R;
        }
}

TEST(Risk, ExpiredOptionsPayIntrinsic) {
    const std::vector<Smile> sm{smile30()};
    const std::vector<Position> book{{1, 55000, 0, true, false}};
    const double v = book_values(book, sm, {{std::log(1.1), 0, 1, 31.0 / 365}}, 1.0)[0];
    EXPECT_NEAR(v, 60000 * 1.1 - 55000, 1e-6);
}
