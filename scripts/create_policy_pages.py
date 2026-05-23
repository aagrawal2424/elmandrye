#!/usr/bin/env python3
"""Create Refund Policy, Terms of Service, and Privacy Policy pages on elmandrye.

Pages are created unpublished so they can be reviewed before going live.
Prints the page IDs and admin preview URLs.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gql import call  # noqa: E402

REFUND = """
<h2>Refund Policy</h2>
<p>We want you to love what you order. If you don't, here's how we'll make it right.</p>

<h3>30-Day Unopened Return Window</h3>
<p>You may return any <strong>unopened, unused product in its original packaging</strong> within <strong>30 days</strong> of delivery for a full refund of the product price.</p>
<h4>What counts as "unopened":</h4>
<ul>
  <li>Outer plastic seal intact</li>
  <li>Inner bottle/jar seal unbroken</li>
  <li>No signs of use or damage</li>
</ul>
<h4>What's not eligible:</h4>
<ul>
  <li>Opened or used products</li>
  <li>Products returned after 30 days</li>
  <li>Products without original packaging</li>
  <li>Free gifts or promotional items</li>
</ul>

<h3>How to Initiate a Return</h3>
<ol>
  <li>Email <strong>support@elmandrye.com</strong> with your order number and the reason for return.</li>
  <li>We'll send you a prepaid return shipping label and detailed instructions within 1&ndash;2 business days.</li>
  <li>Ship the product back using the label we provide.</li>
  <li>Once we receive and inspect your return (typically 3&ndash;5 business days), we'll process your refund.</li>
</ol>

<h3>Return Shipping</h3>
<p>For unopened product returns within 30 days, we cover return shipping via the prepaid label we send. For damaged or incorrect items (see below), we always cover shipping.</p>

<h3>Refund Timeline</h3>
<p>After we receive and inspect your return, refunds are issued to your original payment method within <strong>5&ndash;7 business days</strong>. Some banks may take an additional 3&ndash;10 business days to post the refund.</p>

<h3>Damaged, Defective, or Incorrect Items</h3>
<p>If your order arrives damaged, defective, or different from what you ordered, contact us within <strong>7 days of delivery</strong> at <strong>support@elmandrye.com</strong>. Send a photo of the issue along with your order number, and we'll send a replacement at no cost or issue a full refund &mdash; your choice.</p>

<h3>Subscription Orders</h3>
<p>You can pause, skip, modify, or cancel your subscription at any time from your customer account.</p>
<ul>
  <li><strong>Pre-shipment:</strong> Cancellations made before your next billing date won't be charged.</li>
  <li><strong>Post-shipment:</strong> Once a subscription order has shipped, the 30-day unopened return policy above applies.</li>
</ul>

<h3>Questions?</h3>
<p>Email us at <strong>support@elmandrye.com</strong> &mdash; we typically respond within 1 business day.</p>
""".strip()


TERMS = """
<h2>Terms and Conditions</h2>
<p><em>Effective Date: May 20, 2026</em></p>
<p>These Terms and Conditions ("Terms") govern your use of elmandrye.com (the "Site") and the purchase of products from Elm &amp; Rye ("we," "us," "our"). By using our Site or placing an order, you agree to these Terms.</p>

<h3>1. Eligibility</h3>
<p>You must be at least <strong>18 years old</strong> to purchase from this Site. By placing an order, you represent that you meet this age requirement.</p>

<h3>2. Products and Pricing</h3>
<ul>
  <li>All products are subject to availability. We reserve the right to limit quantities, discontinue products, or correct pricing errors at any time without notice.</li>
  <li>Prices are in USD and exclude applicable taxes, shipping, and handling.</li>
  <li>Promotional codes have stated terms and may be revoked at our discretion.</li>
</ul>

<h3>3. Dietary Supplement Disclaimer</h3>
<p>Our products are <strong>dietary supplements</strong> and are <strong>NOT intended to diagnose, treat, cure, or prevent any disease</strong>. These statements have not been evaluated by the Food and Drug Administration (FDA). Consult your physician before using our products if you are pregnant, nursing, taking medication, have a medical condition, or are under 18 years of age.</p>
<p>Individual results vary. Statements about ingredients are based on traditional use and available research and are not medical claims.</p>

<h3>4. Orders and Payment</h3>
<ul>
  <li>Submitting an order constitutes an offer to purchase. We reserve the right to accept or decline any order.</li>
  <li>We accept payment via the methods listed at checkout. You authorize us to charge your chosen payment method for the total order amount.</li>
  <li>Subscription orders authorize recurring charges at the cadence you select until you cancel from your account.</li>
</ul>

<h3>5. Shipping</h3>
<ul>
  <li>We typically ship within 1&ndash;3 business days. Delivery times depend on carrier and destination.</li>
  <li>Risk of loss passes to you upon delivery to the carrier.</li>
</ul>

<h3>6. Returns and Refunds</h3>
<p>See our <a href="/pages/refund-policy">Refund Policy</a>.</p>

<h3>7. Intellectual Property</h3>
<p>All content on this Site &mdash; including text, images, logos, designs, and software &mdash; is owned by Elm &amp; Rye or our licensors and is protected by copyright, trademark, and other laws. You may not copy, modify, distribute, or use any content without our written permission.</p>

<h3>8. User Conduct</h3>
<p>You agree not to:</p>
<ul>
  <li>Use the Site for any unlawful purpose</li>
  <li>Attempt to gain unauthorized access to systems</li>
  <li>Use bots, scrapers, or automated tools without permission</li>
  <li>Submit false information or impersonate others</li>
  <li>Interfere with the Site's operation</li>
</ul>

<h3>9. Reviews and Submissions</h3>
<p>By submitting a review or any other content, you grant Elm &amp; Rye a perpetual, worldwide, royalty-free license to use, reproduce, and display that content. We may edit or remove reviews at our discretion.</p>

<h3>10. Limitation of Liability</h3>
<p>To the maximum extent permitted by law, Elm &amp; Rye, its officers, employees, and affiliates shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the Site or our products. Our total liability for any claim shall not exceed the amount you paid for the product giving rise to the claim.</p>

<h3>11. Indemnification</h3>
<p>You agree to indemnify and hold Elm &amp; Rye harmless from any claims, damages, or expenses arising from your violation of these Terms or misuse of our products.</p>

<h3>12. Governing Law</h3>
<p>These Terms are governed by the laws of the <strong>State of Delaware</strong>, without regard to conflict-of-laws principles. Any disputes shall be resolved exclusively in the state or federal courts located in Delaware.</p>

<h3>13. Changes to These Terms</h3>
<p>We may update these Terms at any time. Continued use of the Site after changes constitutes acceptance of the updated Terms. The "Effective Date" above indicates the most recent revision.</p>

<h3>14. Contact</h3>
<p>Questions about these Terms? Email <strong>support@elmandrye.com</strong>.</p>
""".strip()


PRIVACY = """
<h2>Privacy Policy</h2>
<p><em>Effective Date: May 20, 2026</em></p>
<p>Elm &amp; Rye ("we," "us," "our") respects your privacy. This Privacy Policy explains what information we collect, how we use it, and your rights.</p>

<h3>1. Information We Collect</h3>
<h4>Information you provide:</h4>
<ul>
  <li><strong>Account information:</strong> name, email address, password</li>
  <li><strong>Order information:</strong> shipping address, billing address, payment method (processed by our payment processor, not stored on our servers)</li>
  <li><strong>Communications:</strong> messages you send us via email, chat, or contact forms</li>
  <li><strong>Survey/quiz responses:</strong> answers you provide when using our supplement-recommendation quiz</li>
</ul>
<h4>Information collected automatically:</h4>
<ul>
  <li><strong>Device info:</strong> IP address, browser type, operating system</li>
  <li><strong>Usage data:</strong> pages visited, products viewed, time spent on the Site</li>
  <li><strong>Cookies and similar technologies:</strong> see Cookies section below</li>
</ul>

<h3>2. How We Use Your Information</h3>
<ul>
  <li>Fulfill orders and process payments</li>
  <li>Send order confirmations, shipping updates, and customer-service responses</li>
  <li>Send marketing emails (which you can unsubscribe from at any time)</li>
  <li>Personalize product recommendations</li>
  <li>Detect and prevent fraud</li>
  <li>Comply with legal obligations</li>
  <li>Improve our products and Site</li>
</ul>

<h3>3. How We Share Your Information</h3>
<p>We share information with:</p>
<ul>
  <li><strong>Service providers:</strong> payment processors (Shopify Payments, Stripe), shipping carriers (USPS, UPS, FedEx), email service providers, analytics tools (Google Analytics, Meta), subscription platform (SKIO), reviews platform (TrustReviews)</li>
  <li><strong>Legal requirements:</strong> when required by law, subpoena, or to protect our rights</li>
  <li><strong>Business transfers:</strong> if we are acquired or merge with another company, customer data may transfer to the new entity</li>
</ul>
<p>We <strong>do not sell</strong> your personal information.</p>

<h3>4. Cookies</h3>
<p>We use cookies and similar tracking technologies to keep you logged in, remember items in your cart, analyze how the Site is used, and personalize content and ads. You can disable cookies in your browser settings, but some Site features may not work properly.</p>

<h3>5. Your Rights</h3>
<h4>All customers:</h4>
<ul>
  <li>Access the personal information we have about you</li>
  <li>Correct inaccurate information</li>
  <li>Request deletion of your account and data (subject to legal record-keeping requirements)</li>
  <li>Opt out of marketing emails (use the unsubscribe link in any email)</li>
</ul>
<h4>California residents (CCPA/CPRA):</h4>
<p>You have additional rights including the right to know what we collect, the right to delete, the right to correct, and the right to non-discrimination for exercising these rights. To submit a request, email <strong>support@elmandrye.com</strong>.</p>
<h4>EU/UK residents (GDPR):</h4>
<p>You have additional rights to data portability, restriction of processing, and to lodge a complaint with your local data protection authority. To submit a request, email <strong>support@elmandrye.com</strong>.</p>

<h3>6. Data Security</h3>
<p>We use industry-standard security measures including SSL encryption, secure payment processors, and access controls. However, no method of transmission over the internet is 100% secure, and we cannot guarantee absolute security.</p>

<h3>7. Data Retention</h3>
<p>We retain your data as long as your account is active or as needed to provide services. We may retain certain information longer to comply with legal obligations, resolve disputes, or enforce our agreements.</p>

<h3>8. Children's Privacy</h3>
<p>Our Site is not intended for anyone under 18. We do not knowingly collect information from children. If we learn we have collected information from a child, we will delete it.</p>

<h3>9. International Users</h3>
<p>If you access our Site from outside the United States, your information may be transferred to, stored in, and processed in the United States. By using our Site, you consent to this transfer.</p>

<h3>10. Changes to This Policy</h3>
<p>We may update this Privacy Policy from time to time. The "Effective Date" above indicates the most recent revision. Continued use of the Site after changes constitutes acceptance.</p>

<h3>11. Contact</h3>
<p>Questions or requests regarding your privacy? Email <strong>support@elmandrye.com</strong>.</p>
""".strip()


PAGES = [
    {"title": "Refund Policy", "handle": "refund-policy", "body": REFUND},
    {"title": "Terms of Service", "handle": "terms-of-service", "body": TERMS},
    {"title": "Privacy Policy", "handle": "privacy", "body": PRIVACY},
]


MUTATION = """
mutation CreatePage($page: PageCreateInput!) {
  pageCreate(page: $page) {
    page { id title handle isPublished }
    userErrors { field message }
  }
}
"""


def main():
    for p in PAGES:
        result = call(MUTATION, {"page": {
            "title": p["title"],
            "handle": p["handle"],
            "body": p["body"],
            "isPublished": False,
        }})
        if result.get("errors") or result["data"]["pageCreate"]["userErrors"]:
            print(f"FAILED {p['title']}: {json.dumps(result, indent=2)}", file=sys.stderr)
            sys.exit(1)
        page = result["data"]["pageCreate"]["page"]
        print(f"  {page['title']:25s} {page['id']}  /pages/{page['handle']}  published={page['isPublished']}")


if __name__ == "__main__":
    main()
