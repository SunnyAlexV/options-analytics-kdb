// Black-76 pricing, implied volatility and Greeks for European options on a forward.
//
// Conventions
// -----------
//   F      forward price of the underlying for the option's expiry (USD)
//   K      strike (USD)
//   T      time to expiry in years (ACT/365, measured to 08:00 UTC on expiry day for Deribit)
//   sigma  volatility as a decimal (0.45 = 45%)
//   r      continuously compounded rate used only for discounting, D = exp(-rT).
//          Deribit uses r = 0 because each expiry's forward already embeds carry.
//   call   true for a call, false for a put
//
// All Greeks are derivatives of the USD option value with the FORWARD held fixed:
//   delta = dV/dF, gamma = d2V/dF2, vega = dV/dsigma (per 1.00 of vol, i.e. per 100 vol points),
//   theta = dV/dt = -dV/dT (per year), rho = dV/dr,
//   vanna = d2V/dF dsigma, volga = d2V/dsigma2, charm = d(delta)/dt, veta = d(vega)/dt,
//   speed = d(gamma)/dF, zomma = d(gamma)/dsigma, colour = d(gamma)/dt.
// "per year" quantities become per calendar day by dividing by 365 (crypto trades every day).
#pragma once

#include <cstdint>

namespace oak {

struct Greeks {
    double price, delta, gamma, vega, theta, rho;
    double vanna, volga, charm, veta, speed, zomma, colour;
};

// Status codes returned by implied_vol.
enum class IVStatus : std::int8_t {
    Ok = 0,
    BelowIntrinsic = 1,   // price at/below intrinsic (no time value left, < 1e-14 of F or K):
                          // no vol can be implied from it
    AboveMaximum = 2,     // price at/above the upper bound (D*F for a call): no finite vol
    NoConvergence = 3,    // did not converge within the iteration limit (should not happen)
    BadInput = 4,         // non-positive F, K or T, or a non-finite input
};

struct IVResult {
    double sigma;         // NaN unless status == Ok
    IVStatus status;
    int iterations;
};

double norm_cdf(double x);
double norm_pdf(double x);

double price(double F, double K, double T, double sigma, double r, bool call);
Greeks greeks(double F, double K, double T, double sigma, double r, bool call);

// Safeguarded Newton-Raphson: Newton steps using vega, kept inside a shrinking
// bracket [lo, hi] that always contains the root; any step that would leave the
// bracket (or a vanishing vega) is replaced by bisection. Converges for every
// price strictly inside the no-arbitrage bounds.
IVResult implied_vol(double target_price, double F, double K, double T, double r, bool call,
                     double tol = 1e-12, int max_iter = 100);

}  // namespace oak
