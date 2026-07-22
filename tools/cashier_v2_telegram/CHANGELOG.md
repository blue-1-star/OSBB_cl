# Changelog

## v0.4.1

- Search is now the first action after opening the cashier.
- Night/Day is inferred from the found vehicle.
- Apartment search expands to the apartment's vehicles.
- Payment type is requested only when it cannot be inferred.
- Tariff lookup now checks open charge, service item default, service price version, and service tariff.
- Zero or unknown amount is blocked from saving.
- Card immediately shows the found payer and proposed values.
- Fixed amount edit logic so manual changes clear stale automatic allocation.
