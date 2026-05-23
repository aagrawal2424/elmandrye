#!/usr/bin/env python3
"""Update /pages/ai-concierge to use the real agent photo URLs.

After uploading the 4 agent images via upload_file.py, run this to
regenerate the page body with proper background-image styles per agent
card (replacing the placeholder gradient cards from initial creation).

Hard-coded photo URLs from the elmandrye Shopify Files CDN.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gql import call  # noqa: E402

PAGE_ID = "gid://shopify/Page/138014949588"

AGENTS = [
    {
        "name": "Mike",
        "role": "Supplement Strategist",
        "bio": "PhD-trained in nutrigenomics. Builds your daily Elm &amp; Rye stack from your labs and goals.",
        "photo": "https://cdn.shopify.com/s/files/1/0522/5309/8141/files/agent_mike.jpg?v=1779568842",
    },
    {
        "name": "Kathy",
        "role": "Diet &amp; Nutrition",
        "bio": "RDN with 10+ years clinical practice. Plans meals around your labs, training, and the food you'll actually cook.",
        "photo": "https://cdn.shopify.com/s/files/1/0522/5309/8141/files/kathy_agent.jpg?v=1779568856",
    },
    {
        "name": "Tristan",
        "role": "Workouts",
        "bio": "Certified S&amp;C coach. Programs your week based on recovery, schedule, and the strength you actually need.",
        "photo": "https://cdn.shopify.com/s/files/1/0522/5309/8141/files/tristan_agent.jpg?v=1779568872",
    },
    {
        "name": "Angelica",
        "role": "Recovery",
        "bio": "Sleep, stress &amp; HRV specialist. Reads your Oura/Whoop data and tells you when to push and when to rest.",
        "photo": "https://cdn.shopify.com/s/files/1/0522/5309/8141/files/Angelica_agent.jpg?v=1779568879",
    },
]


def agent_card(a):
    return f"""
      <div class="aic-agent">
        <div class="aic-agent-photo" style="background-image: url('{a['photo']}'); background-position: center top;"></div>
        <h3>{a['name']}</h3>
        <p class="aic-role">{a['role']}</p>
        <p class="aic-bio">{a['bio']}</p>
      </div>
    """


BODY = """
<style>
  .aic-wrap { font-family: inherit; color: #1a1a1a; line-height: 1.5; }
  .aic-wrap * { box-sizing: border-box; }
  .aic-wrap h1, .aic-wrap h2, .aic-wrap h3 { color: #1a1a1a; }

  .template-page .page__title, .template-page .page-header__title { display: none; }

  .aic-hero { background: linear-gradient(180deg, #f7f1e5 0%, #ece1c8 100%); padding: 80px 24px 100px; text-align: center; border-radius: 10px; margin-bottom: 40px; }
  .aic-kicker { display: inline-block; font-size: 12px; letter-spacing: 2px; text-transform: uppercase; color: #6b5b3a; margin-bottom: 16px; font-weight: 700; }
  .aic-hero h1 { font-size: clamp(36px, 6vw, 60px); font-weight: 700; line-height: 1.05; margin: 0 0 20px; letter-spacing: -1.2px; }
  .aic-hero .aic-lead { font-size: clamp(16px, 2vw, 20px); line-height: 1.5; max-width: 640px; margin: 0 auto 36px; color: #3a3a3a; }
  .aic-cta { display: inline-block; background: #1a1a1a; color: #fff !important; padding: 16px 32px; border-radius: 4px; text-decoration: none; font-weight: 700; font-size: 15px; letter-spacing: 0.5px; transition: transform 0.15s ease; }
  .aic-cta:hover { transform: translateY(-1px); }

  .aic-section { padding: 60px 24px; max-width: 1100px; margin: 0 auto; }
  .aic-section h2 { font-size: clamp(28px, 4vw, 40px); font-weight: 700; text-align: center; margin: 0 0 12px; letter-spacing: -0.5px; }
  .aic-subhead { text-align: center; color: #555; max-width: 620px; margin: 0 auto 48px; font-size: 17px; }

  .aic-team-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 28px; }
  .aic-agent { text-align: center; }
  .aic-agent-photo { width: 100%; aspect-ratio: 1/1; background: #e4dccc; border-radius: 8px; margin-bottom: 18px; background-size: cover; background-position: center; overflow: hidden; }
  .aic-agent h3 { font-size: 22px; margin: 0 0 4px; font-weight: 700; }
  .aic-role { font-size: 12px; letter-spacing: 1.5px; text-transform: uppercase; color: #6b5b3a; margin: 0 0 12px; font-weight: 700; }
  .aic-bio { font-size: 15px; line-height: 1.55; color: #444; margin: 0; max-width: 280px; margin-left: auto; margin-right: auto; }

  .aic-how-bg { background: #faf6ec; border-radius: 10px; margin: 20px 0; }
  .aic-steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 36px; max-width: 1000px; margin: 0 auto; }
  .aic-step { text-align: center; padding: 0 12px; }
  .aic-step .aic-num { display: inline-block; font-size: 12px; letter-spacing: 2px; color: #b39156; font-weight: 700; margin-bottom: 14px; }
  .aic-step h3 { font-size: 22px; margin: 0 0 10px; font-weight: 700; }
  .aic-step p { font-size: 15px; line-height: 1.55; color: #444; margin: 0; }

  .aic-price-card { max-width: 480px; margin: 0 auto; background: #fff; border: 1px solid #d4d4d4; border-radius: 8px; padding: 40px 32px; text-align: center; box-shadow: 0 6px 24px rgba(0,0,0,0.05); }
  .aic-price { font-size: 56px; font-weight: 700; letter-spacing: -2px; color: #1a1a1a; }
  .aic-price small { font-size: 18px; font-weight: 500; color: #777; letter-spacing: 0; }
  .aic-includes { list-style: none; padding: 0; margin: 24px 0 16px; text-align: left; }
  .aic-includes li { padding: 10px 0 10px 28px; border-top: 1px solid #f0ece2; font-size: 15px; position: relative; line-height: 1.45; }
  .aic-includes li:first-child { border-top: 0; }
  .aic-includes li::before { content: "✓"; position: absolute; left: 4px; top: 10px; color: #6b5b3a; font-weight: 700; }
  .aic-founders { background: #fff5db; border: 1px solid #f0deaa; padding: 14px 20px; border-radius: 6px; font-size: 14px; color: #6b5b3a; margin-top: 20px; font-weight: 600; }

  .aic-waitlist-bg { background: #1a1a1a; color: #fff; padding: 80px 24px; border-radius: 10px; margin-top: 40px; }
  .aic-waitlist-bg h2 { color: #fff; }
  .aic-waitlist-bg .aic-subhead { color: rgba(255,255,255,0.7); }
  .aic-form { display: flex; gap: 12px; max-width: 500px; margin: 0 auto; flex-wrap: wrap; }
  .aic-form input[type="email"] { flex: 1; min-width: 220px; padding: 16px 18px; font-size: 16px; border: 1px solid rgba(255,255,255,0.25); background: rgba(255,255,255,0.06); color: #fff; border-radius: 4px; }
  .aic-form input[type="email"]::placeholder { color: rgba(255,255,255,0.5); }
  .aic-form button { padding: 16px 28px; background: #fff; color: #1a1a1a; border: 0; border-radius: 4px; font-weight: 700; font-size: 15px; cursor: pointer; letter-spacing: 0.5px; }
  .aic-form button:hover { background: #f7f1e5; }
  .aic-microcopy { text-align: center; font-size: 13px; color: rgba(255,255,255,0.5); margin-top: 20px; }
  .aic-success { background: rgba(75,160,120,0.15); border: 1px solid rgba(75,160,120,0.4); color: #c8e6d4; padding: 18px 24px; border-radius: 4px; max-width: 500px; margin: 0 auto; text-align: center; font-size: 16px; }
</style>

<div class="aic-wrap">

  <section class="aic-hero">
    <span class="aic-kicker">Coming Soon · Limited Founders Spots</span>
    <h1>Your AI Health Concierge Team</h1>
    <p class="aic-lead">Four AI specialists working on your health 24/7. Built on your bloodwork, your wearables, your goals. Powered by Elm &amp; Rye.</p>
    <a class="aic-cta" href="#waitlist">Join the Waitlist →</a>
  </section>

  <section class="aic-section">
    <h2>Meet your team</h2>
    <p class="aic-subhead">Each agent specializes in one area of your health. Together, they coordinate the full picture.</p>
    <div class="aic-team-grid">
""" + "\n".join(agent_card(a) for a in AGENTS) + """
    </div>
  </section>

  <section class="aic-how-bg">
    <div class="aic-section">
      <h2>How it works</h2>
      <p class="aic-subhead">Three steps from "no idea what I'm doing" to a personalized plan that updates every day.</p>

      <div class="aic-steps">
        <div class="aic-step">
          <span class="aic-num">01 · INTAKE</span>
          <h3>Tell us about you</h3>
          <p>10-minute survey: age, goals, current routine, conditions, what you eat, how you sleep, what you've tried.</p>
        </div>
        <div class="aic-step">
          <span class="aic-num">02 · DATA</span>
          <h3>Order labs or sync wearables</h3>
          <p>We recommend the bloodwork, genome, and microbiome panels that actually matter for your goals. Or sync Oura, Whoop, or Apple Health — we'll work with what you have.</p>
        </div>
        <div class="aic-step">
          <span class="aic-num">03 · YOUR PLAN</span>
          <h3>Daily, in your dashboard</h3>
          <p>Open the dashboard, see what to take, eat, do, and rest. Ask any agent anything. They update your plan as your data changes.</p>
        </div>
      </div>
    </div>
  </section>

  <section class="aic-section">
    <h2>Launch pricing</h2>
    <p class="aic-subhead">Honest, transparent. Cancel anytime.</p>

    <div class="aic-price-card">
      <div class="aic-price">$29.99<small>/month</small></div>
      <ul class="aic-includes">
        <li>4 AI agents coordinating your plan daily</li>
        <li>Personalized supplement, food, training, and recovery recommendations</li>
        <li>Lab result interpretation &amp; tracking</li>
        <li>Wearable integrations (Oura, Whoop, Apple Health)</li>
        <li><strong>$10/mo credit toward any Elm &amp; Rye product</strong></li>
      </ul>
      <div class="aic-founders">🎟️ First 500 founders get 50% off forever — $14.99/mo locked in</div>
    </div>
  </section>

  <section id="waitlist" class="aic-waitlist-bg">
    <div class="aic-section" style="padding-top: 0; padding-bottom: 0;">
      <h2>Be first in line</h2>
      <p class="aic-subhead">Drop your email — we'll let you know when your team is ready and lock in your founders pricing.</p>

      <form id="aic-waitlist-form" class="aic-form" method="post" action="/contact#contact_form" accept-charset="UTF-8">
        <input type="hidden" name="form_type" value="customer">
        <input type="hidden" name="utf8" value="✓">
        <input type="hidden" name="contact[note]" value="AI Concierge Waitlist signup (from /pages/ai-concierge)">
        <input type="hidden" name="contact[tags]" value="ai-concierge-waitlist">
        <input type="email" name="contact[email]" required placeholder="your@email.com" aria-label="Email address">
        <button type="submit">Reserve my spot →</button>
      </form>

      <div id="aic-success" class="aic-success" style="display:none; margin-top:24px;">
        Thanks! You're on the list — we'll email you when your team is ready and your founders discount is locked in.
      </div>

      <p class="aic-microcopy">No spam. Unsubscribe anytime. We'll only email you about the AI Concierge launch.</p>
    </div>
  </section>

</div>

<script>
  (function() {
    var params = new URLSearchParams(window.location.search);
    if (params.get('customer_posted') === 'true') {
      var form = document.getElementById('aic-waitlist-form');
      var success = document.getElementById('aic-success');
      if (form) form.style.display = 'none';
      if (success) success.style.display = 'block';
      setTimeout(function() {
        var w = document.getElementById('waitlist');
        if (w) w.scrollIntoView({behavior:'smooth', block:'center'});
      }, 150);
    }
  })();
</script>
""".strip()


MUTATION = """
mutation UpdatePage($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) {
    page { id title handle isPublished }
    userErrors { field message }
  }
}
"""


def main():
    result = call(MUTATION, {"id": PAGE_ID, "page": {"body": BODY}})
    if result.get("errors") or result["data"]["pageUpdate"]["userErrors"]:
        print(f"FAILED: {json.dumps(result, indent=2)}", file=sys.stderr)
        sys.exit(1)
    page = result["data"]["pageUpdate"]["page"]
    print(f"Updated: {page['id']}  /pages/{page['handle']}  published={page['isPublished']}")
    nid = page['id'].split('/')[-1]
    print(f"Admin: https://elmandrye.myshopify.com/admin/pages/{nid}")


if __name__ == "__main__":
    main()
