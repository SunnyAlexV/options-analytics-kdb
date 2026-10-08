// Black-76 implementation. The derivation of every formula is in lessons/04-pricing.md.
#include "oak/black76.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace oak {

namespace {
constexpr double kInvSqrt2 = 0.70710678118654752440;
constexpr double kInvSqrt2Pi = 0.39894228040143267794;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

struct D12 { double d1, d2, sqrtT; };

D12 d12(double F, double K, double T, double sigma) {
    const double sqrtT = std::sqrt(T);
    const double sd = sigma * sqrtT;                      // total std-dev of ln(F_T)
    const double d1 = (std::log(F / K) + 0.5 * sd * sd) / sd;
    return {d1, d1 - sd, sqrtT};
}
}  // namespace

// N(x) via erfc: accurate in both tails (1 - erf would lose all digits for x << 0).
double norm_cdf(double x) { return 0.5 * std::erfc(-x * kInvSqrt2); }
double norm_pdf(double x) { return kInvSqrt2Pi * std::exp(-0.5 * x * x); }

double price(double F, double K, double T, double sigma, double r, bool call) {
    const double D = std::exp(-r * T);
    if (T <= 0.0 || sigma <= 0.0) {                        // expired / zero vol: discounted intrinsic
        return D * std::max(call ? F - K : K - F, 0.0);
    }
    const auto [d1, d2, sqrtT] = d12(F, K, T, sigma);
    return call ? D * (F * norm_cdf(d1) - K * norm_cdf(d2))
                : D * (K * norm_cdf(-d2) - F * norm_cdf(-d1));
}

Greeks greeks(double F, double K, double T, double sigma, double r, bool call) {
    Greeks g{};
    const double D = std::exp(-r * T);
    const auto [d1, d2, sqrtT] = d12(F, K, T, sigma);
    const double nd1 = norm_pdf(d1);
    const double sst = sigma * sqrtT;

    g.price = price(F, K, T, sigma, r, call);
    g.delta = call ? D * norm_cdf(d1) : -D * norm_cdf(-d1);
    g.gamma = D * nd1 / (F * sst);
    g.vega = D * F * nd1 * sqrtT;
    g.theta = -D * F * nd1 * sigma / (2.0 * sqrtT) + r * g.price;
    g.rho = -T * g.price;                                   // forward held fixed: only discounting moves
    g.vanna = -D * nd1 * d2 / sigma;
    g.volga = g.vega * d1 * d2 / sigma;
    g.charm = r * g.delta + D * nd1 * d2 / (2.0 * T);
    g.veta = g.vega * (r - (1.0 + d1 * d2) / (2.0 * T));
    g.speed = -g.gamma / F * (1.0 + d1 / sst);
    g.zomma = g.gamma * (d1 * d2 - 1.0) / sigma;
    g.colour = g.gamma * (r + (1.0 - d1 * d2) / (2.0 * T));
    return g;
}

IVResult implied_vol(double target, double F, double K, double T, double r, bool call,
                     double tol, int max_iter) {
    if (!(F > 0.0 && K > 0.0 && T > 0.0) || !std::isfinite(target) || !std::isfinite(r)) {
        return {kNaN, IVStatus::BadInput, 0};
    }
    const double D = std::exp(-r * T);

    // Work with the out-of-the-money option. Its price is pure time value, so it
    // stays well-conditioned; an ITM price is mostly intrinsic value, and tiny
    // errors in it become large errors in vol. Put-call parity: C - P = D(F - K).
    bool otm_call = K >= F;
    double p = target;
    if (call != otm_call) p = call ? target - D * (F - K) : target + D * (F - K);

    // No-arbitrage bounds for the OTM option: 0 < p < D*F (call) or D*K (put).
    const double upper = otm_call ? D * F : D * K;
    // A time value below ~1e-14 of the strike/forward is indistinguishable from zero
    // in double precision: treat it as intrinsic only.
    if (p <= 1e-14 * D * std::min(F, K)) return {kNaN, IVStatus::BelowIntrinsic, 0};
    if (p >= upper) return {kNaN, IVStatus::AboveMaximum, 0};

    // Bracket: price is strictly increasing in sigma.
    double lo = 1e-8, hi = 20.0;                            // 0% .. 2,000% vol
    if (price(F, K, T, hi, r, otm_call) < p) return {kNaN, IVStatus::AboveMaximum, 0};

    // Initial guess: Brenner-Subrahmanyam ATM approximation C ~ D*F*sigma*sqrt(T/2pi),
    // and the Manaster-Koehler point sqrt(2|ln(F/K)|/T), where vega is largest.
    const double bs = std::sqrt(6.283185307179586 / T) * p / (D * F);
    const double mk = std::sqrt(2.0 * std::fabs(std::log(F / K)) / T);
    double s = std::clamp(std::max(bs, mk), 0.01, 5.0);

    for (int i = 1; i <= max_iter; ++i) {
        const auto [d1, d2, sqrtT] = d12(F, K, T, s);
        const double v = price(F, K, T, s, r, otm_call);
        const double f = v - p;
        // Relative tolerance: a deep OTM option may be worth 1e-5 USD, so an absolute
        // tolerance scaled to F would stop long before the vol is accurate.
        if (std::fabs(f) <= tol * p) return {s, IVStatus::Ok, i};
        if (f > 0.0) hi = s; else lo = s;                   // shrink the bracket around the root
        if (hi - lo <= 1e-15 * hi) return {s, IVStatus::Ok, i};
        const double vega = D * F * norm_pdf(d1) * sqrtT;
        double next = s - f / vega;                          // Newton step
        if (!(vega > 0.0) || !(next > lo && next < hi)) {
            next = 0.5 * (lo + hi);                          // safeguard: bisect
        }
        s = next;
    }
    return {kNaN, IVStatus::NoConvergence, max_iter};
}

}  // namespace oak
