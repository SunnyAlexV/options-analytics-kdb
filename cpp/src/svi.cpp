// SVI calibration. Derivations in lessons/05-surface.md.
#include "oak/svi.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <limits>
#include <vector>

namespace oak {

// ------------------------------------------------------------------ model
double svi_w(const SVI& p, double k) {
    const double x = k - p.m;
    return p.a + p.b * (p.rho * x + std::sqrt(x * x + p.sigma * p.sigma));
}
double svi_dw(const SVI& p, double k) {
    const double x = k - p.m;
    return p.b * (p.rho + x / std::sqrt(x * x + p.sigma * p.sigma));
}
double svi_d2w(const SVI& p, double k) {
    const double x = k - p.m;
    const double s = std::sqrt(x * x + p.sigma * p.sigma);
    return p.b * p.sigma * p.sigma / (s * s * s);
}
double svi_g(const SVI& p, double k) {
    const double w = svi_w(p, k), w1 = svi_dw(p, k), w2 = svi_d2w(p, k);
    if (w <= 0) return -1.0;                      // negative variance: as bad as it gets
    const double t = 1.0 - k * w1 / (2.0 * w);
    return t * t - 0.25 * w1 * w1 * (1.0 / w + 0.25) + 0.5 * w2;
}

namespace {

constexpr double kInf = std::numeric_limits<double>::infinity();
constexpr double kMarginG = 1e-4;     // target g(k) >= 1e-4, so g(k) >= 0 holds with room to spare
constexpr double kLeeMax = 2.0;       // Lee (2004): asymptotic slope of total variance <= 2
constexpr double kMarginLee = 1e-4;
constexpr double kMarginCal = 1e-4;   // target w(k) >= w_prev(k) * (1 + 1e-4)

// Solve A x = b (n <= 8) by Gaussian elimination with partial pivoting. false if singular.
bool solve(std::vector<double> A, std::vector<double> b, int n, std::vector<double>& x) {
    for (int c = 0; c < n; ++c) {
        int piv = c;
        for (int r = c + 1; r < n; ++r)
            if (std::fabs(A[r * n + c]) > std::fabs(A[piv * n + c])) piv = r;
        if (std::fabs(A[piv * n + c]) < 1e-14) return false;
        if (piv != c) {
            for (int j = 0; j < n; ++j) std::swap(A[c * n + j], A[piv * n + j]);
            std::swap(b[c], b[piv]);
        }
        for (int r = c + 1; r < n; ++r) {
            const double f = A[r * n + c] / A[c * n + c];
            for (int j = c; j < n; ++j) A[r * n + j] -= f * A[c * n + j];
            b[r] -= f * b[c];
        }
    }
    x.assign(n, 0.0);
    for (int r = n - 1; r >= 0; --r) {
        double s = b[r];
        for (int j = r + 1; j < n; ++j) s -= A[r * n + j] * x[j];
        x[r] = s / A[r * n + r];
    }
    return true;
}

double kmin_of(const Slice& s) { return *std::min_element(s.k.begin(), s.k.end()); }
double kmax_of(const Slice& s) { return *std::max_element(s.k.begin(), s.k.end()); }

// Per-point weight in vol space: extra weight / max(half-spread, floor)^2.
std::vector<double> vol_weights(const Slice& s, const FitConfig& cfg) {
    std::vector<double> w(s.k.size());
    for (size_t i = 0; i < w.size(); ++i) {
        const double h = std::max(s.hs[i], cfg.hs_floor);
        w[i] = (s.wt.empty() ? 1.0 : s.wt[i]) / (h * h);
    }
    return w;
}

// ---------------------------------------------------- stage 1: inner QP
// For fixed (m, sigma): w_i ~ a + d*y_i + c*z_i with y = (k-m)/sigma, z = sqrt(y^2+1).
// Zeliade's domain: 0 <= c <= 4 sigma, |d| <= c, |d| <= 4 sigma - c, 0 <= a <= max w_i,
// written as G x <= h for x = (a, d, c). The QP is convex, so its optimum lies on some
// face of the feasible polytope: we solve the equality-constrained problem for every
// set of <= 3 active constraints (42 tiny systems) and keep the best feasible point.
struct Inner { double a, d, c, obj; };

Inner inner_qp(const std::vector<double>& k, const std::vector<double>& w,
               const std::vector<double>& wt, double m, double sigma, double wmax) {
    const int n = static_cast<int>(k.size());
    std::array<double, 9> H{};    // A' W A
    std::array<double, 3> g{};    // A' W w
    std::vector<std::array<double, 3>> rows(n);
    for (int i = 0; i < n; ++i) {
        const double y = (k[i] - m) / sigma, z = std::sqrt(y * y + 1.0);
        rows[i] = {1.0, y, z};
        for (int r = 0; r < 3; ++r) {
            g[r] += wt[i] * rows[i][r] * w[i];
            for (int c = 0; c < 3; ++c) H[r * 3 + c] += wt[i] * rows[i][r] * rows[i][c];
        }
    }
    const double S4 = 4.0 * sigma;
    const double G[6][3] = {{-1, 0, 0}, {1, 0, 0}, {0, 1, -1}, {0, -1, -1}, {0, 1, 1}, {0, -1, 1}};
    const double h[6] = {0, wmax, 0, 0, S4, S4};

    auto objective = [&](const double* x) {
        double o = 0;
        for (int i = 0; i < n; ++i) {
            const double r = w[i] - (x[0] * rows[i][0] + x[1] * rows[i][1] + x[2] * rows[i][2]);
            o += wt[i] * r * r;
        }
        return o;
    };
    auto feasible = [&](const double* x) {
        for (int j = 0; j < 6; ++j) {
            const double v = G[j][0] * x[0] + G[j][1] * x[1] + G[j][2] * x[2];
            if (v > h[j] + 1e-12 * (1.0 + std::fabs(h[j]))) return false;
        }
        return true;
    };

    Inner best{0, 0, 0, kInf};
    std::vector<int> act;
    std::vector<double> sol;
    // enumerate subsets of size 0..3 via bitmasks over 6 constraints
    for (int mask = 0; mask < 64; ++mask) {
        act.clear();
        for (int j = 0; j < 6; ++j) if (mask >> j & 1) act.push_back(j);
        if (act.size() > 3) continue;
        const int na = static_cast<int>(act.size()), N = 3 + na;
        std::vector<double> A(N * N, 0.0), b(N, 0.0);
        for (int r = 0; r < 3; ++r) {
            for (int c = 0; c < 3; ++c) A[r * N + c] = H[r * 3 + c];
            b[r] = g[r];
        }
        for (int q = 0; q < na; ++q) {
            for (int c = 0; c < 3; ++c) {
                A[(3 + q) * N + c] = G[act[q]][c];
                A[c * N + 3 + q] = G[act[q]][c];
            }
            b[3 + q] = h[act[q]];
        }
        if (!solve(A, b, N, sol)) continue;
        if (!feasible(sol.data())) continue;
        const double o = objective(sol.data());
        if (o < best.obj) best = {sol[0], sol[1], sol[2], o};
    }
    return best;
}

SVI from_inner(const Inner& in, double m, double sigma) {
    const double b = in.c / sigma;
    const double rho = in.c > 1e-14 ? std::clamp(in.d / in.c, -0.999999, 0.999999) : 0.0;
    return {in.a, b, rho, m, sigma};
}

// Plain Nelder-Mead in 2-D.
std::array<double, 2> nelder_mead(const std::function<double(const std::array<double, 2>&)>& f,
                                  std::array<double, 2> x0, std::array<double, 2> step, int iters) {
    std::array<std::array<double, 2>, 3> v{x0, x0, x0};
    v[1][0] += step[0];
    v[2][1] += step[1];
    std::array<double, 3> fv{f(v[0]), f(v[1]), f(v[2])};
    for (int it = 0; it < iters; ++it) {
        std::array<int, 3> o{0, 1, 2};
        std::sort(o.begin(), o.end(), [&](int i, int j) { return fv[i] < fv[j]; });
        const auto best = v[o[0]], mid = v[o[1]], worst = v[o[2]];
        const double fb = fv[o[0]], fm = fv[o[1]], fw = fv[o[2]];
        if (std::fabs(fw - fb) <= 1e-14 * (std::fabs(fb) + 1e-300)) break;
        const std::array<double, 2> c{(best[0] + mid[0]) / 2, (best[1] + mid[1]) / 2};
        auto pt = [&](double t) { return std::array<double, 2>{c[0] + t * (worst[0] - c[0]), c[1] + t * (worst[1] - c[1])}; };
        const auto r = pt(-1.0); const double fr = f(r);
        if (fr < fb) {
            const auto e = pt(-2.0); const double fe = f(e);
            if (fe < fr) { v[o[2]] = e; fv[o[2]] = fe; } else { v[o[2]] = r; fv[o[2]] = fr; }
        } else if (fr < fm) {
            v[o[2]] = r; fv[o[2]] = fr;
        } else {
            const auto cc = fr < fw ? pt(-0.5) : pt(0.5);
            const double fc = f(cc);
            if (fc < std::min(fr, fw)) { v[o[2]] = cc; fv[o[2]] = fc; }
            else {                                    // shrink towards the best vertex
                for (int i : {o[1], o[2]}) {
                    v[i] = {(v[i][0] + best[0]) / 2, (v[i][1] + best[1]) / 2};
                    fv[i] = f(v[i]);
                }
            }
        }
    }
    int bi = 0;
    for (int i = 1; i < 3; ++i) if (fv[i] < fv[bi]) bi = i;
    return v[bi];
}

// ---------------------------------------------------- stage 2: LM
// Parameters mapped so the optimiser is unconstrained:
//   th = (a, ln b, atanh rho, m, ln sigma)
SVI from_theta(const std::array<double, 5>& t) {
    return {t[0], std::exp(t[1]), std::tanh(t[2]), t[3], std::exp(t[4])};
}
std::array<double, 5> to_theta(const SVI& p) {
    return {p.a, std::log(std::max(p.b, 1e-8)), std::atanh(std::clamp(p.rho, -0.999999, 0.999999)),
            p.m, std::log(std::max(p.sigma, 1e-6))};
}

struct Problem {
    const Slice& s;
    std::vector<double> wv;       // vol-space weights
    std::vector<double> grid;     // arbitrage check points
    std::vector<double> wprev;    // previous slice's w on the grid (calendar)
    double lambda = 0;            // arbitrage penalty weight (0 = raw fit)

    // Residual vector: data residuals, then penalty residuals.
    void residuals(const SVI& p, std::vector<double>& r) const {
        r.clear();
        for (size_t i = 0; i < s.k.size(); ++i) {
            const double w = svi_w(p, s.k[i]);
            const double vol = std::sqrt(std::max(w, 1e-16) / s.T);
            r.push_back(std::sqrt(wv[i]) * (vol - s.iv[i]));
        }
        // minimum variance must be non-negative, always (also for the raw fit)
        const double minw = p.a + p.b * p.sigma * std::sqrt(std::max(1.0 - p.rho * p.rho, 0.0));
        r.push_back(1e4 * std::max(0.0, -minw));
        if (lambda > 0) {
            const double sl = std::sqrt(lambda);
            // Each constraint is targeted with a small safety margin: a finite penalty always
            // leaves a residual violation where the data's pull balances it, and the margin
            // makes that residual fall on the safe side of the true constraint.
            for (size_t j = 0; j < grid.size(); ++j) {
                r.push_back(sl * std::max(0.0, kMarginG - svi_g(p, grid[j])));
                if (!wprev.empty())
                    r.push_back(sl * std::max(0.0, wprev[j] * (1.0 + kMarginCal) - svi_w(p, grid[j])) / s.T);
            }
            // Lee's moment formula: total variance grows at most 2|k| in the wings. Raw SVI's
            // wing slopes are b(1 +- rho), so b(1 + |rho|) <= 2 (aimed just inside, like the others).
            r.push_back(sl * std::max(0.0, p.b * (1.0 + std::fabs(p.rho)) - kLeeMax * (1.0 - kMarginLee)));
        }
    }
    double cost(const SVI& p) const {
        std::vector<double> r;
        residuals(p, r);
        double c = 0;
        for (double v : r) c += v * v;
        return c;
    }
};

// Levenberg-Marquardt with a numerical Jacobian (5 parameters, so it is cheap).
std::array<double, 5> levenberg_marquardt(const Problem& pr, std::array<double, 5> th, int max_iter, int& iters) {
    std::vector<double> r0, r1;
    pr.residuals(from_theta(th), r0);
    double c0 = 0;
    for (double v : r0) c0 += v * v;
    double mu = 1e-3;
    const int m = static_cast<int>(r0.size());
    std::vector<double> J(m * 5);
    for (iters = 0; iters < max_iter; ++iters) {
        for (int j = 0; j < 5; ++j) {                 // central-difference Jacobian
            const double h = 1e-6 * std::max(1.0, std::fabs(th[j]));
            auto tp = th, tm = th;
            tp[j] += h; tm[j] -= h;
            pr.residuals(from_theta(tp), r1);
            std::vector<double> r2;
            pr.residuals(from_theta(tm), r2);
            for (int i = 0; i < m; ++i) J[i * 5 + j] = (r1[i] - r2[i]) / (2 * h);
        }
        std::vector<double> JtJ(25, 0.0), Jtr(5, 0.0);
        for (int i = 0; i < m; ++i)
            for (int a = 0; a < 5; ++a) {
                Jtr[a] += J[i * 5 + a] * r0[i];
                for (int b = 0; b < 5; ++b) JtJ[a * 5 + b] += J[i * 5 + a] * J[i * 5 + b];
            }
        bool improved = false;
        for (int tries = 0; tries < 20 && !improved; ++tries) {
            auto A = JtJ;
            for (int a = 0; a < 5; ++a) A[a * 5 + a] += mu * std::max(JtJ[a * 5 + a], 1e-12);
            std::vector<double> rhs(5), step;
            for (int a = 0; a < 5; ++a) rhs[a] = -Jtr[a];
            if (!solve(A, rhs, 5, step)) { mu *= 10; continue; }
            std::array<double, 5> tn;
            for (int a = 0; a < 5; ++a) tn[a] = th[a] + step[a];
            pr.residuals(from_theta(tn), r1);
            double c1 = 0;
            for (double v : r1) c1 += v * v;
            if (std::isfinite(c1) && c1 < c0) {
                const double rel = (c0 - c1) / std::max(c0, 1e-300);
                th = tn; r0 = r1; c0 = c1; mu = std::max(mu / 3, 1e-12); improved = true;
                if (rel < 1e-12) return th;
            } else {
                mu *= 4;
            }
        }
        if (!improved) break;
    }
    return th;
}

std::vector<double> make_grid(double kmin, double kmax, double margin, int n) {
    const double span = std::max(kmax - kmin, 0.05);
    const double lo = kmin - margin * span, hi = kmax + margin * span;
    std::vector<double> g(n);
    for (int i = 0; i < n; ++i) g[i] = lo + (hi - lo) * i / (n - 1);
    return g;
}

}  // namespace

// ------------------------------------------------------------------ public
SVI svi_quasi_explicit(const Slice& s, const FitConfig& cfg) {
    const auto wv = vol_weights(s, cfg);
    std::vector<double> w(s.k.size()), ww(s.k.size());
    for (size_t i = 0; i < w.size(); ++i) {
        w[i] = s.iv[i] * s.iv[i] * s.T;
        // weight in variance space: dw = 2 sigma T dsigma  =>  weight_w = weight_vol / (2 sigma T)^2
        const double dv = 2.0 * std::max(s.iv[i], 1e-4) * s.T;
        ww[i] = wv[i] / (dv * dv);
    }
    const double wmax = *std::max_element(w.begin(), w.end());
    const double kmin = kmin_of(s), kmax = kmax_of(s), span = std::max(kmax - kmin, 0.05);
    const double mlo = kmin - span, mhi = kmax + span;

    // outer objective over (m, ln sigma); out-of-bounds points are rejected
    auto f = [&](const std::array<double, 2>& x) {
        const double m = x[0], sg = std::exp(x[1]);
        if (m < mlo || m > mhi || sg < 1e-3 || sg > 5.0) return kInf;
        return inner_qp(s.k, w, ww, m, sg, wmax).obj;
    };
    // multi-start: a 7 x 6 grid of (m, sigma), then Nelder-Mead from the best 3
    std::vector<std::pair<double, std::array<double, 2>>> starts;
    for (int i = 0; i < 7; ++i)
        for (double sg : {0.01, 0.03, 0.08, 0.15, 0.3, 0.6}) {
            const std::array<double, 2> x{kmin + (kmax - kmin) * i / 6.0, std::log(sg)};
            starts.push_back({f(x), x});
        }
    std::sort(starts.begin(), starts.end(), [](auto& a, auto& b) { return a.first < b.first; });
    std::array<double, 2> best = starts[0].second;
    double fbest = kInf;
    for (int i = 0; i < 3 && i < static_cast<int>(starts.size()); ++i) {
        const auto x = nelder_mead(f, starts[i].second, {0.1 * span, 0.5}, 300);
        const double fx = f(x);
        if (fx < fbest) { fbest = fx; best = x; }
    }
    const double m = best[0], sg = std::exp(best[1]);
    return from_inner(inner_qp(s.k, w, ww, m, sg, wmax), m, sg);
}

ArbDiag svi_diagnose(const SVI& p, const SVI* prev, double klo, double khi, int n) {
    ArbDiag d{kInf, 0.0, p.b * (1.0 + std::fabs(p.rho))};
    for (int i = 0; i < n; ++i) {
        const double k = klo + (khi - klo) * i / (n - 1);
        d.min_g = std::min(d.min_g, svi_g(p, k));
        if (prev) d.max_cal = std::max(d.max_cal, svi_w(*prev, k) - svi_w(p, k));
    }
    return d;
}

FitResult svi_fit(const Slice& s, const FitConfig& cfg, const SVI* init) {
    FitResult out{};
    const int n = static_cast<int>(s.k.size());
    if (n < 5 || s.T <= 0) { out.ok = false; return out; }

    Problem pr{s, vol_weights(s, cfg), make_grid(kmin_of(s), kmax_of(s), cfg.margin, cfg.grid), {}, 0.0};
    if (cfg.prev) for (double k : pr.grid) pr.wprev.push_back(svi_w(*cfg.prev, k));

    SVI p0 = init ? *init : svi_quasi_explicit(s, cfg);
    int it = 0, total = 0;
    auto th = levenberg_marquardt(pr, to_theta(p0), cfg.max_iter, it);
    total += it;
    if (cfg.arb_free) {   // penalty continuation: raise the penalty until violations vanish
        for (double lam : {1e2, 1e4, 1e6, 1e8, 1e10, 1e12}) {
            pr.lambda = lam;
            th = levenberg_marquardt(pr, th, cfg.max_iter, it);
            total += it;
            const auto d = svi_diagnose(from_theta(th), cfg.prev, pr.grid.front(), pr.grid.back(), cfg.grid);
            if (d.min_g >= 0.0 && d.max_cal <= 0.0 && d.lee <= kLeeMax) break;
        }
    }
    out.p = from_theta(th);
    out.iterations = total;

    double se = 0, swe = 0, inside = 0;
    for (int i = 0; i < n; ++i) {
        const double vol = std::sqrt(std::max(svi_w(out.p, s.k[i]), 1e-16) / s.T);
        const double e = vol - s.iv[i];
        se += e * e;
        const double h = std::max(s.hs[i], cfg.hs_floor);
        swe += (e / h) * (e / h);
        out.cost += pr.wv[i] * e * e;
        if (std::fabs(e) <= s.hs[i]) inside += 1;
    }
    out.rmse_vol = std::sqrt(se / n);
    out.wrmse = std::sqrt(swe / n);
    out.inside_band = inside / n;
    const auto d = svi_diagnose(out.p, cfg.prev, pr.grid.front(), pr.grid.back(), cfg.grid);
    out.min_g = d.min_g;
    out.max_cal = d.max_cal;
    out.lee = d.lee;
    out.ok = std::isfinite(out.cost);
    return out;
}

}  // namespace oak
