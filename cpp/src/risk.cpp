#include "oak/risk.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "oak/black76.hpp"

namespace oak {

namespace {
constexpr double kMinVol = 1e-4;   // a scenario can push vols down; keep them positive
}

double smile_vol(const Smile& s, double K, double dlnF, double R) {
    // k' = ln(K/F'), F' = F e^x;  look up the old smile at k' + R x = ln(K/F) - (1 - R) x
    const double k = std::log(K / s.F) - (1.0 - R) * dlnF;
    return std::sqrt(std::max(svi_w(s.p, k), 0.0) / s.T);
}

double smile_slope(const Smile& s, double K) {
    const double k = std::log(K / s.F);
    const double sig = std::sqrt(std::max(svi_w(s.p, k), 1e-300) / s.T);
    return svi_dw(s.p, k) / (2.0 * sig * s.T);
}

double position_value(const Position& pos, const std::vector<Smile>& smiles,
                      const Scenario& sc, double R) {
    if (pos.slice < 0 || static_cast<std::size_t>(pos.slice) >= smiles.size())
        throw std::out_of_range("position refers to an unknown expiry");
    const Smile& s = smiles[static_cast<std::size_t>(pos.slice)];
    const double F1 = s.F * std::exp(sc.dlnF);
    if (pos.future) return pos.qty * (F1 - pos.K);
    const double T1 = s.T - sc.dt;
    if (T1 <= 0.0) {                                            // expired: intrinsic value
        const double intrinsic = pos.call ? F1 - pos.K : pos.K - F1;
        return pos.qty * std::max(intrinsic, 0.0);
    }
    const double vol = std::max(smile_vol(s, pos.K, sc.dlnF, R) * sc.vscale + sc.dvol, kMinVol);
    return pos.qty * price(F1, pos.K, T1, vol, 0.0, pos.call);
}

std::vector<double> book_values(const std::vector<Position>& book, const std::vector<Smile>& smiles,
                                const std::vector<Scenario>& scenarios, double R) {
    std::vector<double> out(scenarios.size(), 0.0);
    for (std::size_t j = 0; j < scenarios.size(); ++j) {
        double v = 0.0;
        for (const auto& pos : book) v += position_value(pos, smiles, scenarios[j], R);
        out[j] = v;
    }
    return out;
}

}  // namespace oak
