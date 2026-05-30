We need an online bookstore. Here are the requirements from the team chat:

## Core Features

Users should be able to browse books by category, search by title or author,
and see book details with cover image, description, price, and availability.

Customers need a shopping cart — add books, change quantities, remove items.
Cart should persist between sessions for logged-in users.

Checkout flow: enter shipping address → choose payment method → review order → confirm.
Support credit card and PayPal. Send email confirmation after purchase.

Users need accounts — register with email, log in, reset password, view order history.
Admins need a dashboard to add/edit books, manage inventory, view sales reports.

## Non-Functional

- Page load under 2 seconds
- Handle 1000 concurrent users during sales
- All payments must be PCI-DSS compliant
- GDPR compliant for EU customers (right to deletion, data export)

## Questions from the team

- Should we do pre-orders for upcoming books?
- Do we need reviews and ratings?
- Mobile app or responsive web first?
