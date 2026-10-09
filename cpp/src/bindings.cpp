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
}
