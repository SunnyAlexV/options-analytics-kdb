// Python bindings (pybind11): array-in, array-out wrappers around oak::black76.
// The Python package `pricing` (pricing/__init__.py) broadcasts inputs to equal
// length 1-D float64 arrays before calling these, so here every input has the
// same length n and we simply loop. One call prices a whole option chain.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <cstdint>
#include <initializer_list>
#include <stdexcept>
#include <string>

#include "oak/black76.hpp"

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
}
