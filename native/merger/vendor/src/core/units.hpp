#pragma once

namespace physics::units {

// Optional astronomical SI conversion constants; galaxy scenes use explicit code units.
// Orbit tests pass an explicit GM. G_codata_2018 is a labeled constant, not a hidden default.

constexpr double meter = 1.0;
constexpr double second = 1.0;
constexpr double kilogram = 1.0;

constexpr double kilometre = 1000.0 * meter;
constexpr double au = 149597870700.0;       // IAU 2012 astronomical unit, exact
constexpr double day = 86400.0 * second;    // SI day
constexpr double julian_year = 365.25 * day;

// CODATA 2018 Newtonian constant, m^3 kg^-1 s^-2. Relative uncertainty is about 2.2e-5.
constexpr double G_codata_2018 = 6.67430e-11;

}  // namespace physics::units
