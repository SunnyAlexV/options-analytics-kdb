// GoogleTest suite for oak::svi.
#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>

#include "oak/black76.hpp"
#include "oak/svi.hpp"

using namespace oak;

namespace {
const SVI kTypical{0.02, 0.15, -0.35, 0.05, 0.20};             // a crypto-like skewed smile, T ~ 0.25
// Gatheral & Jacquier (2014), "Arbitrage-free SVI volatility surfaces", example 3.1:
// a raw SVI slice (T = 1) that looks fine but has butterfly arbitrage.
const SVI kGJ{-0.0410, 0.1331, 0.3060, 0.3586, 0.4153};

Slice synthetic(const SVI& p, double T, int n, double noise_vol, double hs, unsigned seed) {
    std::mt19937 rng(seed);
    std::normal_distribution<double> N(0.0, 1.0);
    Slice s;
    s.T = T;
    for (int i = 0; i < n; ++i) {
        const double k = -0.8 + 1.6 * i / (n - 1);
        const double vol = std::sqrt(svi_w(p, k) / T);
        s.k.push_back(k);
        s.iv.push_back(vol + noise_vol * N(rng));
        s.hs.push_back(hs);
        s.wt.push_back(1.0);
    }
    return s;
}
double fd(double (*f)(const SVI&, double), const SVI& p, double k, double h) {
    return (f(p, k + h) - f(p, k - h)) / (2 * h);
}
}  // namespace

TEST(SVI, DerivativesMatchFiniteDifferences) {
    for (double k : {-1.0, -0.3, 0.0, 0.05, 0.4, 1.2}) {
        EXPECT_NEAR(svi_dw(kTypical, k), fd(svi_w, kTypical, k, 1e-6), 1e-8);
        EXPECT_NEAR(svi_d2w(kTypical, k), fd(svi_dw, kTypical, k, 1e-6), 1e-7);
    }
}

// g(k) is the risk-neutral density up to a positive factor:
//   d2C/dK2 = g(k) / (K sqrt(2 pi w)) * exp(-d2^2/2),   d2 = -k/sqrt(w) - sqrt(w)/2   (F = 1)
// Check it against the density obtained by differentiating Black prices twice in strike.
TEST(SVI, DensityConditionMatchesButterflyOfPrices) {
    const double T = 1.0, F = 1.0;
    for (const SVI& p : {kTypical, kGJ}) {
        for (double k : {-0.6, -0.2, 0.0, 0.3, 0.8, 1.2}) {
            const double K = std::exp(k), h = 1e-3 * K;
            auto C = [&](double KK) { return price(F, KK, T, std::sqrt(svi_w(p, std::log(KK)) / T), 0.0, true); };
            const double dens_fd = (C(K + h) - 2 * C(K) + C(K - h)) / (h * h);
            const double w = svi_w(p, k), d2 = -k / std::sqrt(w) - std::sqrt(w) / 2;
            const double dens = svi_g(p, k) / (K * std::sqrt(6.283185307179586 * w)) * std::exp(-0.5 * d2 * d2);
            EXPECT_NEAR(dens, dens_fd, 1e-5 * std::max(1.0, std::fabs(dens))) << "k=" << k;
        }
    }
}

TEST(SVI, GatheralJacquierExampleHasButterflyArbitrage) {
    const auto d = svi_diagnose(kGJ, nullptr, -1.5, 1.5, 301);
    EXPECT_LT(d.min_g, 0.0);
}

TEST(SVI, QuasiExplicitRecoversExactParameters) {
    const double T = 0.25;
    const Slice s = synthetic(kTypical, T, 25, 0.0, 0.005, 1);
    FitConfig cfg;
    const SVI p = svi_quasi_explicit(s, cfg);
    for (double k : s.k)
        EXPECT_NEAR(std::sqrt(svi_w(p, k) / T), std::sqrt(svi_w(kTypical, k) / T), 1e-6) << "k=" << k;
    EXPECT_NEAR(p.rho, kTypical.rho, 1e-3);
    EXPECT_NEAR(p.m, kTypical.m, 1e-3);
}

TEST(SVI, FullFitWithNoiseStaysInsideTheSpread) {
    const double T = 0.25;
    const Slice s = synthetic(kTypical, T, 40, 0.002, 0.006, 7);   // noise 0.2 vol pt, half-spread 0.6
    FitConfig cfg;
    const auto r = svi_fit(s, cfg);
    ASSERT_TRUE(r.ok);
    std::printf("  noisy fit: rmse %.2f vol bp, inside band %.0f%%, wrmse %.2f, iterations %d\n",
                r.rmse_vol * 1e4, 100 * r.inside_band, r.wrmse, r.iterations);
    EXPECT_LT(r.rmse_vol, 0.003);
    EXPECT_GT(r.inside_band, 0.9);
    for (double k : {-0.6, 0.0, 0.6})       // close to the true smile, not just to the noise
        EXPECT_NEAR(std::sqrt(svi_w(r.p, k) / T), std::sqrt(svi_w(kTypical, k) / T), 0.002);
}

TEST(SVI, ArbitrageFreeFitRemovesButterflyArbitrage) {
    const double T = 1.0;
    Slice s = synthetic(kGJ, T, 41, 0.0, 0.004, 3);
    FitConfig raw_cfg;
    const auto raw = svi_fit(s, raw_cfg);
    FitConfig af_cfg;
    af_cfg.arb_free = true;
    const auto af = svi_fit(s, af_cfg);
    std::printf("  raw: min g %.4f rmse %.2f bp | arbitrage-free: min g %.2e rmse %.2f bp\n",
                raw.min_g, raw.rmse_vol * 1e4, af.min_g, af.rmse_vol * 1e4);
    EXPECT_LT(raw.min_g, 0.0);               // the raw fit faithfully reproduces the arbitrage
    EXPECT_GE(af.min_g, 0.0);                // the constrained fit removes it
    EXPECT_LE(af.lee, 4.0 + 1e-9);
    EXPECT_LT(af.rmse_vol, 0.01);            // at a modest cost in fit quality (< 1 vol pt)
}

TEST(SVI, ArbitrageFreeFitRemovesCalendarArbitrage) {
    const double T1 = 0.10, T2 = 0.12;
    const SVI shorter{0.010, 0.12, -0.30, 0.0, 0.15};
    // A longer slice with LOWER total variance in the wings: calendar arbitrage.
    const SVI longer{0.012, 0.06, -0.30, 0.0, 0.15};
    const Slice s = synthetic(longer, T2, 31, 0.0, 0.004, 5);
    FitConfig cfg;
    cfg.prev = &shorter;
    const auto raw = svi_fit(s, cfg);
    cfg.arb_free = true;
    const auto af = svi_fit(s, cfg);
    std::printf("  calendar: raw max violation %.2e | arbitrage-free %.2e (total variance)\n", raw.max_cal, af.max_cal);
    (void)T1;
    EXPECT_GT(raw.max_cal, 1e-4);
    EXPECT_LE(af.max_cal, 0.0);
    EXPECT_GE(af.min_g, 0.0);
}

TEST(SVI, TooFewPointsIsNotOk) {
    Slice s = synthetic(kTypical, 0.25, 4, 0.0, 0.005, 1);
    EXPECT_FALSE(svi_fit(s, FitConfig{}).ok);
}
