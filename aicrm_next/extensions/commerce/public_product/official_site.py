from __future__ import annotations


def render_yuanqi_official_site() -> str:
    """Render the public, read-only company website used for provider review."""

    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="description" content="元气女性成长联盟提供女性成长主题沙龙、沙龙策划与主理人训练服务。">
  <meta name="robots" content="index,follow">
  <title>元气女性成长联盟｜女性成长主题沙龙与主理人训练</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #2f2926;
      --muted: #6f655f;
      --paper: #fffdf9;
      --cream: #f8f0e5;
      --rose: #b95f64;
      --rose-dark: #8c3f45;
      --line: #eaded2;
      --shadow: 0 18px 46px rgba(91, 61, 45, .10);
    }

    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.75;
      text-rendering: optimizeLegibility;
    }
    a { color: inherit; }
    .wrap { width: min(1080px, calc(100% - 40px)); margin: 0 auto; }
    .site-header {
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid rgba(234, 222, 210, .82);
      background: rgba(255, 253, 249, .94);
      backdrop-filter: blur(14px);
    }
    .nav {
      min-height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
    }
    .brand { display: flex; align-items: center; gap: 12px; text-decoration: none; font-weight: 750; }
    .brand-mark {
      width: 38px;
      height: 38px;
      display: grid;
      place-items: center;
      border-radius: 50% 50% 44% 56%;
      color: white;
      background: linear-gradient(145deg, #d57a7e, #9b474d);
      box-shadow: 0 8px 20px rgba(185, 95, 100, .24);
    }
    .nav-links { display: flex; gap: 24px; font-size: 14px; color: var(--muted); }
    .nav-links a { text-decoration: none; }
    .nav-links a:hover, .nav-links a:focus-visible { color: var(--rose-dark); }

    .hero {
      overflow: hidden;
      background:
        radial-gradient(circle at 12% 15%, rgba(213, 122, 126, .17), transparent 31%),
        radial-gradient(circle at 86% 74%, rgba(218, 186, 132, .23), transparent 34%),
        linear-gradient(145deg, #fffaf4 0%, #f7eee4 100%);
    }
    .hero-inner {
      min-height: 560px;
      padding: 96px 0 84px;
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr);
      gap: 64px;
      align-items: center;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 18px;
      color: var(--rose-dark);
      font-size: 14px;
      font-weight: 700;
      letter-spacing: .1em;
    }
    .eyebrow::before { content: ""; width: 28px; height: 1px; background: currentColor; }
    h1 { margin: 0; max-width: 720px; font-size: clamp(38px, 6vw, 68px); line-height: 1.14; letter-spacing: -.035em; }
    .hero-copy { max-width: 660px; margin: 24px 0 0; color: var(--muted); font-size: clamp(17px, 2vw, 20px); }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 34px; }
    .button {
      min-height: 48px;
      padding: 11px 22px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--rose);
      border-radius: 999px;
      text-decoration: none;
      font-weight: 700;
    }
    .button-primary { color: white; background: var(--rose); }
    .button-primary:hover, .button-primary:focus-visible { background: var(--rose-dark); border-color: var(--rose-dark); }
    .button-secondary { color: var(--rose-dark); background: rgba(255, 255, 255, .66); }
    .hero-card {
      padding: 30px;
      border: 1px solid rgba(255, 255, 255, .86);
      border-radius: 28px;
      background: rgba(255, 255, 255, .72);
      box-shadow: var(--shadow);
    }
    .hero-card strong { display: block; margin-bottom: 14px; color: var(--rose-dark); font-size: 18px; }
    .hero-card ul { margin: 0; padding-left: 20px; color: var(--muted); }

    section { padding: 88px 0; }
    .section-soft { background: var(--cream); }
    .section-heading { max-width: 720px; margin-bottom: 42px; }
    .section-heading h2 { margin: 0 0 12px; font-size: clamp(28px, 4vw, 42px); line-height: 1.25; }
    .section-heading p { margin: 0; color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
    .card {
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: #fff;
      box-shadow: 0 10px 30px rgba(76, 52, 39, .05);
    }
    .card-index { color: var(--rose); font-size: 13px; font-weight: 800; letter-spacing: .12em; }
    .card h3 { margin: 10px 0 10px; font-size: 20px; }
    .card p { margin: 0; color: var(--muted); font-size: 15px; }
    .steps { counter-reset: steps; display: grid; gap: 14px; }
    .step {
      counter-increment: steps;
      padding: 22px 24px;
      display: grid;
      grid-template-columns: 44px 1fr;
      gap: 18px;
      align-items: start;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fff;
    }
    .step::before {
      content: counter(steps, decimal-leading-zero);
      color: var(--rose-dark);
      font-weight: 800;
    }
    .step h3 { margin: 0 0 4px; font-size: 18px; }
    .step p { margin: 0; color: var(--muted); font-size: 15px; }
    .policy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    .policy { padding: 28px; border-top: 3px solid var(--rose); background: #fff; }
    .policy h3 { margin: 0 0 12px; }
    .policy p, .policy li { color: var(--muted); font-size: 15px; }
    .policy ul { margin: 0; padding-left: 20px; }
    .notice {
      margin-top: 18px;
      padding: 18px 20px;
      border-radius: 14px;
      color: #5f4f47;
      background: #fff8ee;
      border: 1px solid #ead8bd;
      font-size: 14px;
    }
    .contact {
      padding: 38px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 30px;
      align-items: center;
      border-radius: 26px;
      color: white;
      background: linear-gradient(135deg, #9b474d, #72343a);
      box-shadow: var(--shadow);
    }
    .contact h2 { margin: 0 0 8px; font-size: clamp(26px, 4vw, 38px); }
    .contact p { margin: 0; color: rgba(255,255,255,.83); }
    .contact-badge { padding: 14px 18px; border: 1px solid rgba(255,255,255,.4); border-radius: 16px; white-space: nowrap; }
    .site-footer { padding: 34px 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }
    .footer-inner { display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap; }

    @media (max-width: 780px) {
      .wrap { width: min(100% - 28px, 1080px); }
      .nav-links { display: none; }
      .hero-inner { min-height: auto; padding: 72px 0 64px; grid-template-columns: 1fr; gap: 34px; }
      .hero-card { padding: 24px; }
      section { padding: 64px 0; }
      .grid, .policy-grid { grid-template-columns: 1fr; }
      .contact { padding: 28px; grid-template-columns: 1fr; }
      .contact-badge { white-space: normal; }
    }

    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
    }
  </style>
</head>
<body>
  <header class="site-header">
    <div class="wrap nav">
      <a class="brand" href="#home" aria-label="元气女性成长联盟首页">
        <span class="brand-mark" aria-hidden="true">元</span>
        <span>元气女性成长联盟</span>
      </a>
      <nav class="nav-links" aria-label="主导航">
        <a href="#services">服务介绍</a>
        <a href="#process">服务流程</a>
        <a href="#policies">用户权益</a>
        <a href="#contact">联系我们</a>
      </nav>
    </div>
  </header>

  <main id="home">
    <section class="hero">
      <div class="wrap hero-inner">
        <div>
          <p class="eyebrow">女性成长 · 线下连接 · 主理人训练</p>
          <h1>让每一次相聚，成为真实连接与共同成长的开始</h1>
          <p class="hero-copy">元气女性成长联盟围绕女性成长主题沙龙，为个人与团队提供沙龙策划、主理人训练、线上课程和社群陪跑服务。</p>
          <div class="actions">
            <a class="button button-primary" href="#services">了解服务</a>
            <a class="button button-secondary" href="#contact">咨询报名</a>
          </div>
        </div>
        <aside class="hero-card" aria-label="服务原则">
          <strong>我们的服务原则</strong>
          <ul>
            <li>服务内容、周期与交付方式购买前充分说明</li>
            <li>不承诺收益，不以医疗或心理治疗为服务内容</li>
            <li>尊重个人感受，保护用户信息与表达边界</li>
          </ul>
        </aside>
      </div>
    </section>

    <section id="services">
      <div class="wrap">
        <div class="section-heading">
          <h2>我们提供的服务</h2>
          <p>围绕“内容、带领与落地”三个环节，帮助主理人有序开展主题沙龙与社群活动。</p>
        </div>
        <div class="grid">
          <article class="card">
            <span class="card-index">01 / 主题沙龙</span>
            <h3>女性成长主题活动</h3>
            <p>围绕自我探索、生活美学、亲子互动与节日主题，提供结构化的活动内容与现场体验。</p>
          </article>
          <article class="card">
            <span class="card-index">02 / 主理人训练</span>
            <h3>策划与带领能力</h3>
            <p>覆盖活动定位、流程设计、内容表达、现场组织和复盘改进，支持从学习到实际落地。</p>
          </article>
          <article class="card">
            <span class="card-index">03 / 陪跑支持</span>
            <h3>线上课程与社群答疑</h3>
            <p>通过线上课程、互动研讨与社群答疑，为实际筹备和执行过程提供持续支持。</p>
          </article>
        </div>
      </div>
    </section>

    <section id="process" class="section-soft">
      <div class="wrap">
        <div class="section-heading">
          <h2>服务流程</h2>
          <p>报名与付款前先确认适配性、交付内容和双方权利义务。</p>
        </div>
        <div class="steps">
          <article class="step"><div><h3>咨询了解</h3><p>通过官方公众号联系客服，说明个人情况与学习目标。</p></div></article>
          <article class="step"><div><h3>确认服务</h3><p>客服说明具体课程、服务周期、交付方式、费用及取消退款条件。</p></div></article>
          <article class="step"><div><h3>签署并付款</h3><p>用户确认服务协议和订单信息后，通过官方授权渠道完成付款。</p></div></article>
          <article class="step"><div><h3>服务交付</h3><p>按约定进入课程或服务社群，获取相应内容、答疑与陪跑支持。</p></div></article>
        </div>
      </div>
    </section>

    <section id="policies">
      <div class="wrap">
        <div class="section-heading">
          <h2>用户权益与合规说明</h2>
          <p>以下内容用于帮助用户在购买前了解服务边界，具体约定以双方确认的订单和服务协议为准。</p>
        </div>
        <div class="policy-grid">
          <article class="policy">
            <h3>购买与取消</h3>
            <ul>
              <li>付款前将明确展示服务名称、交付内容、期限与费用。</li>
              <li>如发生重复支付、未按约提供服务等情况，可联系官方客服核实处理。</li>
              <li>课程开始或数字内容交付后的取消、退款，按实际履行情况、服务协议及法律法规处理。</li>
            </ul>
          </article>
          <article class="policy">
            <h3>隐私与信息保护</h3>
            <ul>
              <li>仅在咨询、报名与服务交付所必需的范围内收集信息。</li>
              <li>未经授权，不向无关第三方公开用户个人信息。</li>
              <li>用户可通过官方客服咨询信息查询、更正或删除事宜。</li>
            </ul>
          </article>
        </div>
        <div class="notice"><strong>重要提示：</strong>本平台提供文化交流、成长教育及活动策划相关服务，不提供医疗诊断、心理治疗或投资理财服务，不对个人收入、经营收益或特定结果作出承诺。</div>
      </div>
    </section>

    <section id="contact" class="section-soft">
      <div class="wrap">
        <div class="contact">
          <div>
            <h2>联系官方客服</h2>
            <p>服务咨询、报名、订单与售后问题，请通过官方公众号联系。人工服务时间以公众号公示为准。</p>
          </div>
          <div class="contact-badge">微信公众号：<strong>元气女性成长圈</strong></div>
        </div>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="wrap footer-inner">
      <span>运营主体：武汉闪闪少女文化传播有限公司</span>
      <span>© 2026 元气女性成长联盟</span>
    </div>
  </footer>
</body>
</html>"""


__all__ = ["render_yuanqi_official_site"]
