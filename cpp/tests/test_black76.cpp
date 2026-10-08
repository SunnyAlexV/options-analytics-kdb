// GoogleTest unit tests for oak::black76.
//   cmake -S . -B build -DOAK_BUILD_TESTS=ON && cmake --build build -j && ctest --test-dir build
//
// The strategy: every closed-form result is checked against something derived
// independently -- put-call parity, finite differences ("bump and revalue"),
// or a round trip through the implied-vol solver.
#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <limits>
#include <vector>

#include "oak/black76.hpp"

using oak::greeks;
using oak::implied_vol;
using oak::IVStatus;
using oak::price;

namespace {
struct Case { double F, K, T, sigma, r; };

// Deliberately wide: short- and long-dated, deep ITM/OTM, crypto-level vols, non-zero rates.
const std::vector<Case> kCases = {
    {80000, 80000, 0.25, 0.50, 0.00}, {80000, 60000, 0.10, 0.70, 0.00},
    {80000, 120000, 1.00, 0.45, 0.00}, {80000, 79000, 2.0 / 365, 0.90, 0.00},
    {100, 100, 1.0, 0.20, 0.05},      {100, 80, 0.5, 0.30, 0.03},
    {100, 130, 3.0, 0.25, 0.02},      {80000, 150000, 0.75, 1.20, 0.00},
};

// Central finite difference of f at x with step h.
double fd(const std::function<double(double)>& f, double x, double h) {
    return (f(x + h) - f(x - h)) / (2 * h);
}
}  // namespace

TEST(Black76, PutCallParity) {
    for (const auto& c : kCases) {
        const double C = price(c.F, c.K, c.T, c.sigma, c.r, true);
        const double P = price(c.F, c.K, c.T, c.sigma, c.r, false);
        EXPECT_NEAR(C - P, std::exp(-c.r * c.T) * (c.F - c.K), 1e-9 * c.F);
    }
}

TEST(Black76, KnownValue) {
    // ATM, r = 0: C = F (2N(sigma sqrt(T)/2) - 1). F=100, sigma=20%, T=1 -> 7.965567455405804
    EXPECT_NEAR(price(100, 100, 1.0, 0.2, 0.0, true), 7.965567455405804, 1e-12);
}

TEST(Black76, Bounds) {
    for (const auto& c : kCases) {
        const double D = std::exp(-c.r * c.T);
        const double C = price(c.F, c.K, c.T, c.sigma, c.r, true);
        EXPECT_GT(C, D * std::max(c.F - c.K, 0.0) - 1e-9);
        EXPECT_LT(C, D * c.F);
    }
}

// Every Greek against bump-and-revalue of the quantity it is the derivative of.
TEST(Black76, GreeksMatchFiniteDifferences) {
    for (const auto& c : kCases) {
        for (bool call : {true, false}) {
            const auto g = greeks(c.F, c.K, c.T, c.sigma, c.r, call);
            const double hF = 1e-4 * c.F, hS = 1e-5, hT = 1e-6, hR = 1e-6;
            auto V = [&](double F, double T, double s, double r) { return price(F, c.K, T, s, r, call); };
            auto G = [&](double F, double T, double s) { return greeks(F, c.K, T, s, c.r, call); };
            auto rel = [](double a, double b) { return std::fabs(a - b) / std::max({std::fabs(a), std::fabs(b), 1e-8}); };
            SCOPED_TRACE(::testing::Message() << "F=" << c.F << " K=" << c.K << " T=" << c.T
                                              << " sigma=" << c.sigma << " r=" << c.r << " call=" << call);

            EXPECT_LT(rel(g.delta, fd([&](double x) { return V(x, c.T, c.sigma, c.r); }, c.F, hF)), 1e-6);
            EXPECT_LT(rel(g.gamma, fd([&](double x) { return G(x, c.T, c.sigma).delta; }, c.F, hF)), 1e-5);
            EXPECT_LT(rel(g.vega, fd([&](double x) { return V(c.F, c.T, x, c.r); }, c.sigma, hS)), 1e-6);
            // theta = dV/dt = -dV/dT
            EXPECT_LT(rel(g.theta, -fd([&](double x) { return V(c.F, x, c.sigma, c.r); }, c.T, hT)), 1e-5);
            EXPECT_LT(rel(g.rho, fd([&](double x) { return V(c.F, c.T, c.sigma, x); }, c.r, hR)), 1e-6);
            EXPECT_LT(rel(g.vanna, fd([&](double x) { return G(c.F, c.T, x).delta; }, c.sigma, hS)), 1e-5);
            EXPECT_LT(rel(g.volga, fd([&](double x) { return G(c.F, c.T, x).vega; }, c.sigma, hS)), 1e-5);
            EXPECT_LT(rel(g.charm, -fd([&](double x) { return G(c.F, x, c.sigma).delta; }, c.T, hT)), 1e-4);
            EXPECT_LT(rel(g.veta, -fd([&](double x) { return G(c.F, x, c.sigma).vega; }, c.T, hT)), 1e-4);
            EXPECT_LT(rel(g.speed, fd([&](double x) { return G(x, c.T, c.sigma).gamma; }, c.F, hF)), 1e-5);
            EXPECT_LT(rel(g.zomma, fd([&](double x) { return G(c.F, c.T, x).gamma; }, c.sigma, hS)), 1e-5);
            EXPECT_LT(rel(g.colour, -fd([&](double x) { return G(c.F, x, c.sigma).gamma; }, c.T, hT)), 1e-4);
        }
    }
}

// price -> implied vol -> the same vol, across a grid of moneyness, expiry and vol.
//
// How accurate can it possibly be? A double holds ~16 significant digits, so the
// input price itself is only known to about eps * price. Dividing by vega turns
// that into the smallest vol error any solver could achieve:
//     limit = 4 * eps * price / vega
// For OTM inputs this is tiny. For deep ITM inputs (price ~ intrinsic, vega ~ 0)
// it can reach 1e-8: the information is simply not in the number. So each round
// trip must be within max(1e-10, limit).
TEST(ImpliedVol, RoundTripGrid) {
    int n = 0, worst_iter = 0;
    double worst_ratio = 0, worst_abs = 0;
    const double F = 80000, eps = std::numeric_limits<double>::epsilon();
    for (double k : {-1.5, -0.8, -0.4, -0.1, 0.0, 0.1, 0.4, 0.8, 1.5}) {        // ln(K/F)
        for (double T : {1.0 / 365, 7.0 / 365, 30.0 / 365, 0.25, 1.0, 3.0}) {
            for (double s : {0.05, 0.2, 0.5, 1.0, 2.0, 4.0}) {
                for (bool call : {true, false}) {
                    const double K = F * std::exp(k);
                    const double p = price(F, K, T, s, 0.0, call);
                    // Skip prices that are numerically pure intrinsic (time value below
                    // ~1e-10 of the forward): no solver can recover a vol from them.
                    const double tv = p - std::max(call ? F - K : K - F, 0.0);
                    if (tv < 1e-10 * F) continue;
                    const auto r = implied_vol(p, F, K, T, 0.0, call);
                    ASSERT_EQ(r.status, IVStatus::Ok) << "k=" << k << " T=" << T << " s=" << s;
                    const double err = std::fabs(r.sigma - s);
                    const double limit = std::max(1e-10, 4 * eps * p / greeks(F, K, T, s, 0.0, call).vega);
                    EXPECT_LE(err, limit) << "k=" << k << " T=" << T << " s=" << s << " call=" << call;
                    worst_ratio = std::max(worst_ratio, err / limit);
                    worst_abs = std::max(worst_abs, err);
                    worst_iter = std::max(worst_iter, r.iterations);
                    ++n;
                }
            }
        }
    }
    std::printf("  round trips: %d | worst |sigma error| %.1e | worst error / precision limit %.2f | "
                "worst iterations %d\n", n, worst_abs, worst_ratio, worst_iter);
}

TEST(ImpliedVol, RejectsArbitrageablePrices) {
    const double F = 100, K = 90, T = 0.5;
    EXPECT_EQ(implied_vol(9.0, F, K, T, 0.0, true).status, IVStatus::BelowIntrinsic);   // intrinsic = 10
    EXPECT_EQ(implied_vol(100.0, F, K, T, 0.0, true).status, IVStatus::AboveMaximum);    // = F
    EXPECT_EQ(implied_vol(1.0, F, K, -1.0, 0.0, true).status, IVStatus::BadInput);
    EXPECT_TRUE(std::isnan(implied_vol(9.0, F, K, T, 0.0, true).sigma));
}

TEST(ImpliedVol, NonZeroRate) {
    const double p = price(100, 110, 2.0, 0.3, 0.04, false);
    const auto r = implied_vol(p, 100, 110, 2.0, 0.04, false);
    ASSERT_EQ(r.status, IVStatus::Ok);
    EXPECT_NEAR(r.sigma, 0.3, 1e-10);
}
