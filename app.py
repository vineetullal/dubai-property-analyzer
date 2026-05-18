import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

from config import LOCATIONS, PRICE_MIN, PRICE_MAX, MONTHLY_INCOME
from database import (
    init_db, upsert_property, get_all_properties, get_alerts,
    mark_alerts_read, log_refresh, get_last_refresh, get_market_stats,
    get_price_history,
)
from analyzer import enrich_properties, negotiation_tips, compute_proximity
from mortgage import rank_banks, affordability_summary
from scraper import scrape_all

# ── Page config ────────────────────────────────
st.set_page_config(
    page_title="Dubai Property Analyzer",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0f1117; }
    .metric-card {
        background: linear-gradient(135deg, #1e2130, #252940);
        border-radius: 12px;
        padding: 1.2rem;
        border-left: 4px solid #f5a035;
        margin-bottom: 0.5rem;
    }
    .distress-badge {
        background: #ff4b4b;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: bold;
    }
    .good-value-badge {
        background: #00c853;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
    }
    .stTabs [data-baseweb="tab"] { font-size: 0.9rem; font-weight: 600; }
    .property-card {
        border: 1px solid #333;
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 1rem;
        background: #1a1d2e;
    }
</style>
""", unsafe_allow_html=True)

# ── Initialise DB ──────────────────────────────
init_db()


# ── Sidebar ────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cb/Flag_of_the_United_Arab_Emirates.svg/320px-Flag_of_the_United_Arab_Emirates.svg.png", width=60)
    st.title("Dubai Property Analyzer")
    st.caption("Powered by 20+ years of market intelligence")

    st.divider()
    st.subheader("Filters")

    price_range = st.slider(
        "Price Range (AED M)",
        min_value=1.0, max_value=5.0,
        value=(PRICE_MIN / 1_000_000, PRICE_MAX / 1_000_000),
        step=0.1, format="%.1fM",
    )
    filter_min = int(price_range[0] * 1_000_000)
    filter_max = int(price_range[1] * 1_000_000)

    selected_locations = st.multiselect(
        "Communities",
        options=list(LOCATIONS.keys()),
        default=list(LOCATIONS.keys()),
    )

    bedrooms_filter = st.selectbox(
        "Min Bedrooms",
        options=[0, 1, 2, 3, 4, 5],
        index=0,
        format_func=lambda x: "Any" if x == 0 else f"{x}+",
    )

    prop_type_filter = st.selectbox(
        "Property Type",
        ["All", "Apartment", "Villa", "Townhouse", "Penthouse"],
    )

    distress_only = st.toggle("Distress Deals Only", value=False)

    st.divider()
    st.subheader("Data Refresh")
    last = get_last_refresh()
    if last:
        st.caption(f"Last refresh: {last['ran_at'][:16]}")
        st.caption(f"{last['properties_found']} properties found")

    if st.button("Refresh Data Now", type="primary", use_container_width=True):
        with st.spinner("Scanning Bayut, PropertyFinder & Dubizzle..."):
            progress = st.progress(0)
            status_text = st.empty()

            def progress_cb(pct, msg):
                progress.progress(pct)
                status_text.text(msg)

            props, errors = scrape_all(progress_callback=progress_cb)

            if props:
                enriched = enrich_properties(props)
                new_count = 0
                for p in enriched:
                    is_new = upsert_property(p)
                    if is_new:
                        new_count += 1
                log_refresh(len(props), new_count, "success")
                st.success(f"Found {len(props)} properties ({new_count} new)")
            else:
                log_refresh(0, 0, "no_results")
                if errors:
                    st.warning("Scraping returned no results. Sites may be blocking automated access.")
                    with st.expander("Details"):
                        for e in errors[:5]:
                            st.text(e)

            progress.empty()
            status_text.empty()
            st.rerun()

    st.divider()
    st.caption(f"Monthly income: AED {MONTHLY_INCOME:,}")


# ── Build filters ──────────────────────────────
filters = {
    "min_price": filter_min,
    "max_price": filter_max,
    "locations": selected_locations if selected_locations else list(LOCATIONS.keys()),
    "bedrooms": bedrooms_filter,
    "distress_only": distress_only,
    "property_type": None if prop_type_filter == "All" else prop_type_filter,
}

properties = get_all_properties(filters)
market_stats = get_market_stats()


# ── Header KPIs ────────────────────────────────
st.title("Dubai Property Intelligence Dashboard")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Listings", f"{len(properties):,}")
distress_count = sum(1 for p in properties if p.get("is_distress"))
col2.metric("Distress Deals", distress_count, delta=f"{distress_count} flagged", delta_color="inverse")
avg_price = sum(p["price"] for p in properties) / len(properties) if properties else 0
col3.metric("Avg Price", f"AED {avg_price/1_000_000:.2f}M")
avg_psf = sum(p["price_per_sqft"] for p in properties if p.get("price_per_sqft")) / max(len(properties), 1)
col4.metric("Avg Price/sqft", f"AED {avg_psf:.0f}")
alerts = get_alerts(unread_only=True)
col5.metric("New Alerts", len(alerts), delta="unread")


# ── Tabs ───────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "All Listings", "Distress Deals", "Mortgage Calculator",
    "Market Analysis", "Alerts", "Expert Insights",
])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1 — ALL LISTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab1:
    if not properties:
        st.info("No properties found. Hit 'Refresh Data Now' in the sidebar to scan live listings.")
        st.stop()

    # Sort controls
    sort_col, sort_dir_col, view_col = st.columns([2, 1, 1])
    sort_by = sort_col.selectbox(
        "Sort by",
        ["Price (Low-High)", "Price (High-Low)", "Price/sqft", "Value Score", "Days on Market", "Distress Score"],
    )
    view_mode = view_col.radio("View", ["Table", "Cards"], horizontal=True)

    df = pd.DataFrame(properties)

    sort_map = {
        "Price (Low-High)": ("price", True),
        "Price (High-Low)": ("price", False),
        "Price/sqft": ("price_per_sqft", True),
        "Value Score": ("distress_score", False),
        "Days on Market": ("days_on_market", False),
        "Distress Score": ("distress_score", False),
    }
    col_name, asc = sort_map[sort_by]
    if col_name in df.columns:
        df = df.sort_values(col_name, ascending=asc)

    if view_mode == "Table":
        display_cols = ["title", "location", "price", "bedrooms", "bathrooms",
                        "area_sqft", "price_per_sqft", "days_on_market",
                        "service_charge_estimate", "value_assessment", "source"]
        display_cols = [c for c in display_cols if c in df.columns]
        display_df = df[display_cols].copy()
        display_df.columns = ["Title", "Location", "Price (AED)", "Beds", "Baths",
                               "Area (sqft)", "AED/sqft", "Days Listed",
                               "Est. SC/month", "Value", "Source"][:len(display_cols)]
        display_df["Price (AED)"] = display_df["Price (AED)"].apply(lambda x: f"{x:,.0f}")
        st.dataframe(display_df, use_container_width=True, height=500)
    else:
        # Card view
        for i in range(0, min(len(df), 30), 2):
            c1, c2 = st.columns(2)
            for col_ui, idx in [(c1, i), (c2, i + 1)]:
                if idx >= len(df):
                    continue
                p = df.iloc[idx].to_dict()
                with col_ui:
                    badge = ""
                    if p.get("is_distress"):
                        badge = '<span class="distress-badge">DISTRESS</span>'
                    elif p.get("value_assessment") in ("Excellent Value", "Good Value"):
                        badge = f'<span class="good-value-badge">{p["value_assessment"]}</span>'

                    st.markdown(f"""
                    <div class="property-card">
                        <strong>{p.get("title", "N/A")}</strong> {badge}<br>
                        <small>{p.get("location")} &bull; {p.get("source")}</small><br>
                        <h3 style="margin:0.3rem 0">AED {p.get("price",0):,.0f}</h3>
                        <span>{p.get("bedrooms","?")} Bed &bull; {p.get("bathrooms","?")} Bath &bull; {p.get("area_sqft",0):,.0f} sqft</span><br>
                        <small>AED {p.get("price_per_sqft",0):,.0f}/sqft &bull; {p.get("days_on_market",0)} days listed</small><br>
                        <small>Est. Service Charge: AED {p.get("service_charge_estimate",0):,.0f}/month</small>
                    </div>
                    """, unsafe_allow_html=True)
                    if p.get("url"):
                        st.markdown(f"[View Listing]({p['url']})", unsafe_allow_html=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2 — DISTRESS DEALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab2:
    distress_props = [p for p in properties if p.get("is_distress")]

    if not distress_props:
        st.info("No distress deals detected right now. Refresh data to check for new listings.")
    else:
        st.markdown(f"### {len(distress_props)} Distress Deals Found")
        st.caption("Properties priced significantly below market, with high days-on-market, or with distress keywords in their descriptions.")

        for p in sorted(distress_props, key=lambda x: x.get("distress_score", 0), reverse=True):
            with st.expander(
                f"[Score: {p.get('distress_score', 0):.0f}/100] {p.get('title', 'Unknown')} — AED {p.get('price', 0):,}",
                expanded=p.get("distress_score", 0) >= 70,
            ):
                col_a, col_b = st.columns([2, 1])
                with col_a:
                    st.markdown(f"**Location:** {p.get('location')} | **Source:** {p.get('source')}")
                    st.markdown(f"**Price:** AED {p.get('price', 0):,} | **{p.get('area_sqft', 0):,.0f} sqft** | AED {p.get('price_per_sqft', 0):,.0f}/sqft")
                    st.markdown(f"**Listed for:** {p.get('days_on_market', 0)} days")

                    avg_psf = LOCATIONS.get(p.get("location", ""), {}).get("avg_price_psf", 0)
                    if avg_psf and p.get("price_per_sqft"):
                        discount = (avg_psf - p["price_per_sqft"]) / avg_psf * 100
                        st.markdown(f"**vs Area Average:** AED {avg_psf}/sqft (you save {discount:.1f}%)")

                    st.markdown("**Distress Signals:**")
                    for sig in p.get("distress_signals", []):
                        st.markdown(f"- {sig}")

                with col_b:
                    st.markdown("**Negotiation Tips:**")
                    for tip in negotiation_tips(p):
                        st.markdown(f"- {tip}")

                if p.get("url"):
                    st.link_button("View Full Listing", p["url"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3 — MORTGAGE CALCULATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab3:
    st.markdown("### Mortgage Options — Ranked for Your Profile")

    afford = affordability_summary(MONTHLY_INCOME)

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Max Monthly EMI (50% DBR)", f"AED {afford['max_monthly_emi']:,}")
    col_b.metric("Max Loan Amount", f"AED {afford['max_loan_amount']/1_000_000:.2f}M")
    col_c.metric("Max Property (80% LTV)", f"AED {afford['max_property_price_80ltv']/1_000_000:.2f}M")
    col_d.metric("Min Down Payment (20%)", f"AED {afford['min_down_payment_required']:,}")

    st.divider()

    mortgage_price = st.number_input(
        "Property Price for Calculation (AED)",
        min_value=1_000_000, max_value=5_000_000,
        value=2_750_000, step=50_000, format="%d",
    )
    is_resident = st.toggle("UAE Resident", value=True)

    ranked = rank_banks(mortgage_price, is_resident=is_resident)

    st.markdown("#### All Banks Ranked by Best Fixed Rate")
    for bank in ranked:
        color = "#00c853" if bank["eligible"] else "#ff4b4b"
        eligibility_tag = "ELIGIBLE" if bank["eligible"] else "ABOVE DBR LIMIT"
        bank_type_icon = "☪️" if bank["type"] == "Islamic" else "🏦"

        with st.expander(
            f"#{bank['rank']} {bank_type_icon} {bank['bank']} — {bank['fixed_rate']}% fixed ({bank['fixed_period_years']}yr) | EMI AED {bank['monthly_emi_fixed']:,}",
            expanded=bank["rank"] <= 3 and bank["eligible"],
        ):
            c1, c2, c3 = st.columns(3)
            c1.metric("Fixed Rate", f"{bank['fixed_rate']}%", f"{bank['fixed_period_years']} year(s)")
            c2.metric("Est. Variable Rate", f"{bank['variable_rate_estimate']}%", "After fixed period")
            c3.metric("Monthly EMI (Fixed)", f"AED {bank['monthly_emi_fixed']:,}")

            c4, c5, c6 = st.columns(3)
            c4.metric("Down Payment", f"AED {bank['down_payment']:,}", f"{bank['down_payment_pct']:.0f}%")
            c5.metric("Max Loan", f"AED {bank['max_loan']:,}")
            c6.metric("Processing Fee", f"AED {bank['processing_fee']:,}")

            st.markdown(f"**DBR:** {bank['dbr_pct']}% of income | **Status:** :{('green' if bank['eligible'] else 'red')}[{eligibility_tag}]")
            st.markdown(f"**Total estimated repayment (25yr):** AED {bank['total_repayment_estimate']:,}")
            st.markdown("**Features:** " + " · ".join(bank["features"]))

    st.divider()
    st.markdown("#### Upfront Cost Breakdown")
    costs = afford["total_upfront_costs"]
    cost_df = pd.DataFrame([
        {"Item": "Down Payment (20%)", "Amount (AED)": costs["down_payment_20pct"]},
        {"Item": "DLD Registration Fee (4%)", "Amount (AED)": costs["dld_fee_4pct"]},
        {"Item": "Agent Commission (2%)", "Amount (AED)": costs["agent_commission_2pct"]},
        {"Item": "Mortgage Registration", "Amount (AED)": costs["mortgage_registration"]},
        {"Item": "Bank Processing Fee (1%)", "Amount (AED)": costs["bank_processing_fee_1pct"]},
        {"Item": "Property Valuation", "Amount (AED)": costs["valuation_fee"]},
    ])
    cost_df["Amount (AED)"] = cost_df["Amount (AED)"].apply(lambda x: f"{x:,}")
    st.dataframe(cost_df, use_container_width=True, hide_index=True)
    st.markdown(f"**Total Upfront Estimate: AED {costs['total_estimate']:,}**")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4 — MARKET ANALYSIS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab4:
    st.markdown("### Market Analysis by Community")

    if not properties:
        st.info("No data yet — refresh first.")
    else:
        df_all = pd.DataFrame(properties)

        # Price/sqft by community bar chart
        if "location" in df_all.columns and "price_per_sqft" in df_all.columns:
            psf_df = df_all.groupby("location")["price_per_sqft"].mean().reset_index()
            psf_df.columns = ["Community", "Avg AED/sqft"]

            # Add area benchmark
            benchmarks = pd.DataFrame([
                {"Community": loc, "Benchmark AED/sqft": cfg["avg_price_psf"]}
                for loc, cfg in LOCATIONS.items()
            ])
            psf_df = psf_df.merge(benchmarks, on="Community", how="left")

            fig = go.Figure()
            fig.add_bar(x=psf_df["Community"], y=psf_df["Avg AED/sqft"], name="Current Listings", marker_color="#f5a035")
            fig.add_bar(x=psf_df["Community"], y=psf_df["Benchmark AED/sqft"], name="Area Benchmark", marker_color="#00b0e7")
            fig.update_layout(
                title="Price Per Sqft vs Area Benchmark",
                barmode="group", template="plotly_dark",
                height=400,
            )
            st.plotly_chart(fig, use_container_width=True)

        col_left, col_right = st.columns(2)

        with col_left:
            # Listing count by community
            if "location" in df_all.columns:
                count_df = df_all["location"].value_counts().reset_index()
                count_df.columns = ["Community", "Listings"]
                fig2 = px.pie(count_df, names="Community", values="Listings",
                              title="Listings Distribution", template="plotly_dark",
                              color_discrete_sequence=px.colors.qualitative.Bold)
                st.plotly_chart(fig2, use_container_width=True)

        with col_right:
            # Price distribution
            if "price" in df_all.columns:
                fig3 = px.histogram(
                    df_all, x="price", nbins=30,
                    title="Price Distribution",
                    template="plotly_dark",
                    color_discrete_sequence=["#f5a035"],
                    labels={"price": "Price (AED)"},
                )
                st.plotly_chart(fig3, use_container_width=True)

        # Days on market vs Price
        if "days_on_market" in df_all.columns and "price" in df_all.columns:
            fig4 = px.scatter(
                df_all[df_all["days_on_market"] > 0],
                x="days_on_market", y="price",
                color="location", size="area_sqft",
                title="Days on Market vs Price (bubble = size)",
                template="plotly_dark",
                labels={"days_on_market": "Days on Market", "price": "Price (AED)"},
                hover_data=["title", "price_per_sqft"],
            )
            st.plotly_chart(fig4, use_container_width=True)

        # Market stats table
        if market_stats:
            stats_rows = []
            for loc, s in market_stats.items():
                stats_rows.append({
                    "Community": loc,
                    "Listings": int(s.get("count", 0)),
                    "Avg Price": f"AED {s.get('avg_price', 0):,.0f}",
                    "Avg AED/sqft": f"AED {s.get('avg_psf', 0):,.0f}",
                    "Min Price": f"AED {s.get('min_price', 0):,.0f}",
                    "Max Price": f"AED {s.get('max_price', 0):,.0f}",
                    "Avg Days Listed": f"{s.get('avg_dom', 0):.0f}",
                })
            st.dataframe(pd.DataFrame(stats_rows), use_container_width=True, hide_index=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5 — ALERTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab5:
    all_alerts = get_alerts()
    unread = [a for a in all_alerts if not a["is_read"]]

    col_a, col_b = st.columns([3, 1])
    col_a.markdown(f"### {len(unread)} Unread Alerts ({len(all_alerts)} total)")
    if col_b.button("Mark All Read"):
        mark_alerts_read()
        st.rerun()

    type_icons = {
        "new_listing": "🆕",
        "price_change": "📉",
        "distress_deal": "🔥",
    }

    if not all_alerts:
        st.info("No alerts yet. Alerts are generated when new listings appear or prices change.")
    else:
        for alert in all_alerts[:50]:
            icon = type_icons.get(alert.get("type", ""), "📌")
            read_style = "" if not alert["is_read"] else "opacity: 0.5;"
            created = alert.get("created_at", "")[:16]
            title_link = f"[{alert.get('title', 'View')}]({alert.get('url', '#')})" if alert.get("url") else alert.get("title", "")
            st.markdown(
                f"{icon} **{alert.get('message')}** — {title_link}  \n"
                f"<small style='{read_style}'>{created}</small>",
                unsafe_allow_html=True,
            )
            st.divider()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 6 — EXPERT INSIGHTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab6:
    st.markdown("### Expert Market Intelligence")
    st.caption("20+ years Dubai real estate experience — curated insights for your search")

    st.markdown("""
#### Current Market Overview (2025-2026)

The Dubai residential market remains one of the strongest globally, driven by continued international demand,
a growing expat population, and limited new supply in established communities. Here's what you need to know:

---

#### Community-by-Community Analysis

**The Views** — *Premium, Golf Course Community*
Inventory is low and prices have held firm. Price per sqft typically AED 1,500-1,800 for apartments.
Service charges are higher than average (~AED 18-20/sqft). Best for buyers seeking a quiet, established
community with strong rental yield (~5-5.5%). Negotiate harder on units listed over 90 days.

**Dubai Hills Estate** — *Family-Oriented, Rapidly Appreciating*
One of the fastest appreciating communities since 2021. Villas have seen 40%+ gains.
Townhouses and apartments in the 2.5-3M range offer good long-term value.
Dubai Hills Mall and Kings College Hospital are huge value-adds.
Off-plan in this area has largely dried up — focus on ready units with motivated sellers.

**Arabian Ranches 1** — *Mature Villa Community, Best Value per sqft*
The most affordable per sqft among villa communities. Many original owners bought in 2005-2010
and have significant equity — more room for negotiation.
Look for JESS school catchment area properties for stronger resale.
Service charges are reasonable (~AED 14-16/sqft).

**Studio City** — *Emerging, Strong Rental Demand*
Significant upside potential. Strong demand from media and creative professionals.
Price/sqft still below AED 1,100 in many cases — undervalued relative to nearby Barsha.
Good for investment-minded buyers; 6-7% gross rental yield common.

**Motorcity** — *Best Value, Community Amenities*
Currently the most affordable per sqft on your list.
Established community with F1-X, bowling, cinemas. Strong family demand.
Look for Golf Vista and Pelham communities within Motorcity for best build quality.
Negotiation power is highest here due to longer average days-on-market.

**JLT (Jumeirah Lake Towers)** — *Metro Access, Corporate Demand*
DMCC and Nakheel metro stations make JLT the best-connected community on your list.
Strong corporate rental demand keeps occupancy high.
Prices have risen 20-25% since 2022. Clusters A-G near the lakes command premium.
Cluster Y/Z are older and offer better value — look here for distress deals.

---

#### Timing Your Purchase

- **Q2-Q3** is historically the best time to buy — sellers are more motivated before summer
- Many expat sellers exit before summer, creating a seasonal opportunity window
- End of year (Nov-Dec) also sees motivated sellers wanting to close before January

#### Red Flags to Avoid
- Listings with unclear DLD ownership — always verify via Dubai Land Department
- Properties with >5 years outstanding service charge debt
- Off-plan from developers with less than 3 completed projects in Dubai
- Ground floor units in JLT towers (flooding risk in some clusters)

#### Off-Plan vs Ready
At your budget, ready properties in established communities offer **better security and no completion risk**.
Off-plan at 2.5-3M AED in Dubai Hills or Motorcity may offer payment plan benefits but adds 2-4 year wait.
**Recommendation:** Focus on ready properties given your combined 30K income — stability matters more than upside.
    """)

    st.divider()
    st.markdown("#### Proximity Summary by Community")

    prox_data = []
    for loc, cfg in LOCATIONS.items():
        dummy_prop = {"location": loc, "latitude": cfg.get("lat"), "longitude": cfg.get("lon")}
        prox = compute_proximity(dummy_prop)
        row = {"Community": loc, "Proximity Score": prox["proximity_score"]}
        for cat, data in prox.get("nearest", {}).items():
            row[f"Nearest {cat}"] = f"{data['name']} ({data['distance_km']}km)"
        prox_data.append(row)

    if prox_data:
        st.dataframe(pd.DataFrame(prox_data), use_container_width=True, hide_index=True)
