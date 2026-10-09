// Python bindings (pybind11): array-in, array-out wrappers around oak::black76.
// The Python package `pricing` (pricing/__init__.py) broadcasts inputs to equal
// length 1-D float64 arrays before calling these, so here every input has the
// same length n and we simply loop. One call prices a whole option chain.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <initializer_list>
#include <optional>
#include <vector>
#include <stdexcept>
#include <string>

#include "oak/black76.hpp"
#include "oak/risk.hpp"
#include "oak/svi.hpp"

namespace py = pybind11;
using Arr = py::array_t<double, py::array::c_style | py::array::forcecast>;
using BArr = py::array_t<bool, py::array::c_style | py::array::forcecast>;

namespace {
py::ssize_t common_len(std::initializer_list<py::ssize_t> ns) {
    py::ssize_t n = *ns.begin();
    for (auto m : ns)
        if (m != n) throw std::invalid_argument("all inputs must have the same length");
    return n;
}
}  // namespace

PYBIND11_MODULE(_core, m) {
    m.doc() = "Black-76 pricing, implied vol and Greeks (C++)";

    m.def("price", [](Arr F, Arr K, Arr T, Arr sigma, Arr r, BArr call) {
        const auto n = common_len({F.size(), K.size(), T.size(), sigma.size(), r.size(), call.size()});
        Arr out(n);
        auto f = F.unchecked<1>(); auto k = K.unchecked<1>(); auto t = T.unchecked<1>();
        auto s = sigma.unchecked<1>(); auto rr = r.unchecked<1>(); auto c = call.unchecked<1>();
        auto o = out.mutable_unchecked<1>();
        {
            py::gil_scoped_release nogil;                  // pure C++ from here: let Python threads run
            for (py::ssize_t i = 0; i < n; ++i) o(i) = oak::price(f(i), k(i), t(i), s(i), rr(i), c(i));
        }
        return out;
    });

    m.def("implied_vol", [](Arr P, Arr F, Arr K, Arr T, Arr r, BArr call, double tol, int max_iter) {
        const auto n = common_len({P.size(), F.size(), K.size(), T.size(), r.size(), call.size()});
        Arr sig(n);
        py::array_t<std::int8_t> status(n);
        py::array_t<std::int32_t> iters(n);
        auto p = P.unchecked<1>(); auto f = F.unchecked<1>(); auto k = K.unchecked<1>();
        auto t = T.unchecked<1>(); auto rr = r.unchecked<1>(); auto c = call.unchecked<1>();
        auto so = sig.mutable_unchecked<1>(); auto st = status.mutable_unchecked<1>();
        auto it = iters.mutable_unchecked<1>();
        {
            py::gil_scoped_release nogil;
            for (py::ssize_t i = 0; i < n; ++i) {
                const auto res = oak::implied_vol(p(i), f(i), k(i), t(i), rr(i), c(i), tol, max_iter);
                so(i) = res.sigma;
                st(i) = static_cast<std::int8_t>(res.status);
                it(i) = res.iterations;
            }
        }
        return py::make_tuple(sig, status, iters);
    }, py::arg("price"), py::arg("F"), py::arg("K"), py::arg("T"), py::arg("r"), py::arg("call"),
       py::arg("tol") = 1e-12, py::arg("max_iter") = 100);

    m.def("greeks", [](Arr F, Arr K, Arr T, Arr sigma, Arr r, BArr call) {
        const auto n = common_len({F.size(), K.size(), T.size(), sigma.size(), r.size(), call.size()});
        static const char* names[] = {"price", "delta", "gamma", "vega", "theta", "rho", "vanna",
                                      "volga", "charm", "veta", "speed", "zomma", "colour"};
        constexpr int kN = 13;
        Arr cols[kN];
        for (auto& c : cols) c = Arr(n);
        auto f = F.unchecked<1>(); auto k = K.unchecked<1>(); auto t = T.unchecked<1>();
        auto s = sigma.unchecked<1>(); auto rr = r.unchecked<1>(); auto c = call.unchecked<1>();
        double* out[kN];
        for (int j = 0; j < kN; ++j) out[j] = cols[j].mutable_data();
        {
            py::gil_scoped_release nogil;
            for (py::ssize_t i = 0; i < n; ++i) {
                const oak::Greeks g = oak::greeks(f(i), k(i), t(i), s(i), rr(i), c(i));
                const double v[kN] = {g.price, g.delta, g.gamma, g.vega, g.theta, g.rho, g.vanna,
                                      g.volga, g.charm, g.veta, g.speed, g.zomma, g.colour};
                for (int j = 0; j < kN; ++j) out[j][i] = v[j];
            }
        }
        py::dict d;
        for (int j = 0; j < kN; ++j) d[names[j]] = cols[j];
        return d;
    });

    // ------------------------------------------------------------------ SVI
    auto to_svi = [](const std::vector<double>& v) {
        if (v.size() != 5) throw std::invalid_argument("SVI parameters must be [a, b, rho, m, sigma]");
        return oak::SVI{v[0], v[1], v[2], v[3], v[4]};
    };

    m.def("svi_w", [to_svi](const std::vector<double>& p, Arr k) {
        const auto P = to_svi(p);
        Arr out(k.size());
        auto kk = k.unchecked<1>(); auto o = out.mutable_unchecked<1>();
        for (py::ssize_t i = 0; i < k.size(); ++i) o(i) = oak::svi_w(P, kk(i));
        return out;
    }, "Total variance w(k) for SVI parameters [a, b, rho, m, sigma]");

    m.def("svi_dw", [to_svi](const std::vector<double>& p, Arr k) {
        const auto P = to_svi(p);
        Arr out(k.size());
        auto kk = k.unchecked<1>(); auto o = out.mutable_unchecked<1>();
        for (py::ssize_t i = 0; i < k.size(); ++i) o(i) = oak::svi_dw(P, kk(i));
        return out;
    }, "Smile slope dw/dk for SVI parameters [a, b, rho, m, sigma]");

    m.def("svi_g", [to_svi](const std::vector<double>& p, Arr k) {
        const auto P = to_svi(p);
        Arr out(k.size());
        auto kk = k.unchecked<1>(); auto o = out.mutable_unchecked<1>();
        for (py::ssize_t i = 0; i < k.size(); ++i) o(i) = oak::svi_g(P, kk(i));
        return out;
    }, "Gatheral's density function g(k); negative values mean butterfly arbitrage");

    m.def("fit_svi", [to_svi](Arr k, Arr iv, Arr hs, Arr wt, double T, bool arb_free,
                              std::optional<std::vector<double>> prev,
                              std::optional<std::vector<double>> init, double hs_floor) {
        const auto n = common_len({k.size(), iv.size(), hs.size(), wt.size()});
        oak::Slice s;
        s.T = T;
        s.k.assign(k.data(), k.data() + n);
        s.iv.assign(iv.data(), iv.data() + n);
        s.hs.assign(hs.data(), hs.data() + n);
        s.wt.assign(wt.data(), wt.data() + n);
        oak::FitConfig cfg;
        cfg.arb_free = arb_free;
        cfg.hs_floor = hs_floor;
        oak::SVI prev_p{}, init_p{};
        if (prev) { prev_p = to_svi(*prev); cfg.prev = &prev_p; }
        if (init) init_p = to_svi(*init);
        oak::FitResult r;
        {
            py::gil_scoped_release nogil;
            r = oak::svi_fit(s, cfg, init ? &init_p : nullptr);
        }
        py::dict d;
        d["a"] = r.p.a; d["b"] = r.p.b; d["rho"] = r.p.rho; d["m"] = r.p.m; d["sigma"] = r.p.sigma;
        d["cost"] = r.cost; d["rmse_vol"] = r.rmse_vol; d["wrmse"] = r.wrmse;
        d["inside_band"] = r.inside_band; d["min_g"] = r.min_g; d["max_cal"] = r.max_cal;
        d["lee"] = r.lee; d["iterations"] = r.iterations; d["ok"] = r.ok;
        return d;
    }, py::arg("k"), py::arg("iv"), py::arg("hs"), py::arg("wt"), py::arg("T"),
       py::arg("arb_free") = false, py::arg("prev") = py::none(), py::arg("init") = py::none(),
       py::arg("hs_floor") = 0.0025);

    // ------------------------------------------------------------------ risk
    // smiles: F[n], T[n], P[n x 5] (SVI params per expiry); book: one entry per position;
    // scenarios: one entry per scenario. Returns the book's USD value under each scenario.
    using IArr = py::array_t<std::int64_t, py::array::c_style | py::array::forcecast>;
    using PArr = py::array_t<double, py::array::c_style | py::array::forcecast>;
    auto make_smiles = [](Arr F, Arr T, PArr P) {
        if (P.ndim() != 2 || P.shape(1) != 5 || P.shape(0) != F.size() || T.size() != F.size())
            throw std::invalid_argument("smiles: F[n], T[n] and params[n, 5] required");
        auto f = F.unchecked<1>(); auto t = T.unchecked<1>(); auto p = P.unchecked<2>();
        std::vector<oak::Smile> sm;
        for (py::ssize_t i = 0; i < F.size(); ++i)
            sm.push_back({f(i), t(i), oak::SVI{p(i, 0), p(i, 1), p(i, 2), p(i, 3), p(i, 4)}});
        return sm;
    };

    m.def("book_values", [make_smiles](Arr qty, Arr K, IArr slice, BArr call, BArr future,
                                       Arr F, Arr T, PArr P,
                                       Arr dlnF, Arr dvol, Arr vscale, Arr dt, double R) {
        const auto n = common_len({qty.size(), K.size(), slice.size(), call.size(), future.size()});
        const auto m_ = common_len({dlnF.size(), dvol.size(), vscale.size(), dt.size()});
        const auto sm = make_smiles(F, T, P);
        auto q = qty.unchecked<1>(); auto k = K.unchecked<1>(); auto s = slice.unchecked<1>();
        auto c = call.unchecked<1>(); auto fu = future.unchecked<1>();
        std::vector<oak::Position> book;
        for (py::ssize_t i = 0; i < n; ++i)
            book.push_back({q(i), k(i), static_cast<int>(s(i)), c(i), fu(i)});
        auto x = dlnF.unchecked<1>(); auto dv = dvol.unchecked<1>();
        auto vs = vscale.unchecked<1>(); auto tt = dt.unchecked<1>();
        std::vector<oak::Scenario> sc;
        for (py::ssize_t j = 0; j < m_; ++j) sc.push_back({x(j), dv(j), vs(j), tt(j)});
        std::vector<double> v;
        {
            py::gil_scoped_release nogil;
            v = oak::book_values(book, sm, sc, R);
        }
        Arr out(m_);
        auto o = out.mutable_unchecked<1>();
        for (py::ssize_t j = 0; j < m_; ++j) o(j) = v[static_cast<std::size_t>(j)];
        return out;
    }, "Book value (USD) under each scenario, full revaluation off SVI smiles with stickiness R");

    m.def("smile_vol", [make_smiles](Arr F, Arr T, PArr P, IArr slice, Arr K, double dlnF, double R) {
        const auto sm = make_smiles(F, T, P);
        const auto n = common_len({slice.size(), K.size()});
        auto s = slice.unchecked<1>(); auto k = K.unchecked<1>();
        Arr out(n); auto o = out.mutable_unchecked<1>();
        for (py::ssize_t i = 0; i < n; ++i)
            o(i) = oak::smile_vol(sm.at(static_cast<std::size_t>(s(i))), k(i), dlnF, R);
        return out;
    }, "Implied vol at each strike after a log forward move dlnF, stickiness R");

    m.def("smile_slope", [make_smiles](Arr F, Arr T, PArr P, IArr slice, Arr K) {
        const auto sm = make_smiles(F, T, P);
        const auto n = common_len({slice.size(), K.size()});
        auto s = slice.unchecked<1>(); auto k = K.unchecked<1>();
        Arr out(n); auto o = out.mutable_unchecked<1>();
        for (py::ssize_t i = 0; i < n; ++i)
            o(i) = oak::smile_slope(sm.at(static_cast<std::size_t>(s(i))), k(i));
        return out;
    }, "d sigma / dk of each expiry's smile at each strike");
}
