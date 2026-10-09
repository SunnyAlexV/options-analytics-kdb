// Full revaluation of an options book off fitted SVI smiles, under scenarios.
//
// Market state per expiry ("Smile"): its forward F, time to expiry T, and the fitted SVI
// total-variance smile w(k), k = ln(K/F). Implied vol at strike K is sqrt(w(k)/T).
//
// Smile dynamics: skew-stickiness ratio R (Bergomi). When every forward moves by a log
// amount x = ln(F'/F), the new smile in new log-moneyness k' = ln(K/F') is
//
//     sigma_new(k') = sigma_old(k' + R x)
//
//   R = 1  sticky-strike:    each strike keeps its vol      (k' + x = ln(K/F), the old k)
//   R = 0  sticky-moneyness: the smile moves with the price (sigma_new(k') = sigma_old(k'))
// The ATM vol then moves by  d sigma_ATM = R * skew * x  (skew = d sigma/dk at k = 0),
// which is how R is measured from data.
//
// Positions:
//   option  qty * Black76(F', K, T', sigma')          (USD; 1 contract = 1 BTC underlying)
//   future  qty * (F' - K), K = entry price           (linear USD hedge on that expiry's forward)
#pragma once

#include <vector>

#include "oak/svi.hpp"

namespace oak {

struct Smile {
    double F;     // forward (USD)
    double T;     // years to expiry
    SVI p;        // fitted smile: total variance at T
};

struct Position {
    double qty;   // contracts, negative = short
    double K;     // strike; for a future, its entry price
    int slice;    // index of the expiry in the smiles vector
    bool call;
    bool future;
};

struct Scenario {
    double dlnF = 0;     // log move of every forward
    double dvol = 0;     // additive vol shift (0.01 = 1 vol point), applied after vscale
    double vscale = 1;   // multiplicative vol scale
    double dt = 0;       // time elapsed (years); smiles keep their vols (sticky in time)
};

// Implied vol at strike K on smile s after a log forward move dlnF, under stickiness R
// (before any vol shift).
double smile_vol(const Smile& s, double K, double dlnF, double R);

// d sigma / dk of the smile at strike K (no move): w'(k) / (2 sigma T).
double smile_slope(const Smile& s, double K);

double position_value(const Position& pos, const std::vector<Smile>& smiles,
                      const Scenario& sc, double R);

// Book value (USD) under each scenario: out[j] = sum_i value(pos_i, scenario_j).
std::vector<double> book_values(const std::vector<Position>& book, const std::vector<Smile>& smiles,
                                const std::vector<Scenario>& scenarios, double R);

}  // namespace oak
