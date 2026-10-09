// SVI volatility-smile calibration (raw SVI, Gatheral 2004).
//
// Total implied variance w = sigma_imp^2 * T as a function of log-moneyness k = ln(K/F):
//     w(k) = a + b * ( rho*(k - m) + sqrt((k - m)^2 + sigma^2) )
//   a      level                 b      wing slope (>= 0)
//   rho    skew, in (-1, 1)      m      horizontal shift
//   sigma  curvature at the bottom of the smile (> 0)
//
// Calibration is two-stage:
//   1. quasi_explicit: Zeliade (2009). For fixed (m, sigma) the model is linear in
//      (a, d = rho*b*sigma, c = b*sigma), so the best (a, d, c) is a small convex
//      quadratic programme, solved EXACTLY here. Nelder-Mead searches (m, sigma)
//      from a grid of starting points. Robust: no false minima in 3 of 5 dimensions.
//   2. fit: Levenberg-Marquardt on all 5 parameters, minimising implied-vol errors
//      in units of each quote's half bid-ask spread. Optionally arbitrage-free:
//      penalties drive butterfly (density g(k) >= 0), calendar (w_T >= w_prev) and
//      Roger Lee (b(1+|rho|) <= 4) violations to zero.
#pragma once

#include <vector>

namespace oak {

struct SVI {
    double a, b, rho, m, sigma;
};

double svi_w(const SVI& p, double k);    // total variance
double svi_dw(const SVI& p, double k);   // dw/dk
double svi_d2w(const SVI& p, double k);  // d2w/dk2
// Gatheral's density function. The implied risk-neutral density is proportional to
// g(k) * exp(-d2^2/2)/sqrt(2 pi w), so g(k) < 0 anywhere means butterfly arbitrage.
double svi_g(const SVI& p, double k);

// One expiry's observations.
struct Slice {
    std::vector<double> k;    // log-moneyness ln(K/F)
    std::vector<double> iv;   // mid implied vol (decimal)
    std::vector<double> hs;   // half bid-ask spread in vol (decimal): the measurement uncertainty
    std::vector<double> wt;   // extra weight multiplier per point (1.0 = none)
    double T = 0;             // time to expiry, years
};

struct FitConfig {
    double hs_floor = 0.0025;     // half-spreads below 0.25 vol points are floored: caps any one weight
    bool arb_free = false;        // enforce butterfly / calendar / Lee constraints
    const SVI* prev = nullptr;    // arbitrage-free fit of the previous (shorter) expiry, for calendar
    int grid = 101;               // k-points where arbitrage conditions are checked
    double margin = 0.25;         // grid extends this fraction of the data range beyond the data
    int max_iter = 200;           // LM iterations per penalty level
};

struct FitResult {
    SVI p;
    double cost;         // sum of squared weighted residuals (data part)
    double rmse_vol;     // unweighted RMS implied-vol error (decimal)
    double wrmse;        // RMS error in units of half-spreads
    double inside_band;  // fraction of points whose fitted vol lies within [bid IV, ask IV]
    double min_g;        // minimum of g(k) on the grid (< 0: butterfly arbitrage)
    double max_cal;      // max of w_prev(k) - w(k) on the grid (> 0: calendar arbitrage)
    double lee;          // b(1+|rho|) (must be <= 4)
    int iterations;
    bool ok;
};

// Stage 1 only (useful on its own and for tests). Returns parameters.
SVI svi_quasi_explicit(const Slice& s, const FitConfig& cfg);

// Full calibration: stage 1 (unless init is given) then LM. With cfg.arb_free the
// result is arbitrage-free on the check grid (to within numerical tolerance).
FitResult svi_fit(const Slice& s, const FitConfig& cfg, const SVI* init = nullptr);

// Arbitrage diagnostics of given parameters on [klo, khi].
struct ArbDiag { double min_g, max_cal, lee; };
ArbDiag svi_diagnose(const SVI& p, const SVI* prev, double klo, double khi, int grid);

}  // namespace oak
