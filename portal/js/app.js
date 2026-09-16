/* ===== FDE Hub · Single Page App Router ===== */
(function () {
  "use strict";

  const VIEW = document.getElementById("view");

  // ------- 路由表 -------
  const ROUTES = {
    "/": renderHome,
    "/projects": renderProjects,
    "/project/:id": renderProjectDetail,
    "/category/:cat": renderCategory,
    "/roadmap": renderRoadmap,
    "/about": renderAbout,
  };

  // ------- 启动 -------
  window.addEventListener("load", init);
  window.addEventListener("hashchange", route);

  function init() {
    // 模拟在线人数 (1-50 随机)
    document.getElementById("online-count").textContent =
      Math.floor(15 + Math.random() * 40);
    route();
  }

  function route() {
    const hash = location.hash.replace(/^#/, "") || "/";
    highlightNav(hash);

    // 匹配动态路由
    for (const [pattern, fn] of Object.entries(ROUTES)) {
      const m = match(pattern, hash);
      if (m) {
        fn(m);
        window.scrollTo(0, 0);
        return;
      }
    }
    renderHome();
  }

  function match(pattern, path) {
    const pParts = pattern.split("/").filter(Boolean);
    const hParts = path.split("/").filter(Boolean);
    if (pParts.length !== hParts.length) return null;
    const params = {};
    for (let i = 0; i < pParts.length; i++) {
      if (pParts[i].startsWith(":")) {
        params[pParts[i].slice(1)] = hParts[i];
      } else if (pParts[i] !== hParts[i]) {
        return null;
      }
    }
    return params;
  }

  function highlightNav(hash) {
    document.querySelectorAll(".nav__link").forEach((link) => {
      const route = link.dataset.route;
      if (route && (route === hash || (hash === "/" && route === "/"))) {
        link.classList.add("active");
      } else if (route && hash.startsWith(route) && route !== "/") {
        link.classList.add("active");
      } else {
        link.classList.remove("active");
      }
    });
  }

  // ------- Helpers -------
  function $(sel, root = document) {
    return root.querySelector(sel);
  }
  function $all(sel, root = document) {
    return Array.from(root.querySelectorAll(sel));
  }
  function tpl(id) {
    return document.getElementById(id).innerHTML;
  }
  function el(html) {
    const div = document.createElement("div");
    div.innerHTML = html.trim();
    return div;
  }

  /* ============ PROJECT CARD ============ */
  function renderCardHTML(p) {
    return `
      <a href="#/project/${p.id}" class="card">
        <span class="card__cat ${p.catClass}">${p.catLabel}</span>
        <div class="card__title">${p.title}</div>
        <div class="card__desc">${p.summary}</div>
        <div class="card__stack">
          ${(p.stack || []).slice(0, 4).map((s) => `<span>${s}</span>`).join("")}
          ${(p.stack || []).length > 4 ? `<span>+${p.stack.length - 4}</span>` : ""}
        </div>
        <div class="card__meta">
          <span class="card__tag">${p.status || ""}</span>
          <span>${p.date || ""}</span>
        </div>
      </a>
    `;
  }

  function renderCards(targetId, projects) {
    const node = document.getElementById(targetId);
    if (!node) return;
    if (projects.length === 0) {
      node.innerHTML = "";
      const empty = document.getElementById("empty-hint");
      if (empty) empty.style.display = "block";
      return;
    }
    const empty = document.getElementById("empty-hint");
    if (empty) empty.style.display = "none";
    node.innerHTML = projects.map(renderCardHTML).join("");
  }

  /* ============ HOME ============ */
  function renderHome() {
    VIEW.innerHTML = tpl("tpl-home");

    const cards = window.PROJECTS.slice(0, 6);
    renderCards("home-cards", cards);

    // Tab 切换
    $all("#home-tabs .tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $all("#home-tabs .tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const cat = tab.dataset.cat;
        const filtered =
          cat === "all"
            ? window.PROJECTS.slice(0, 6)
            : window.PROJECTS.filter((p) => p.category === cat).slice(0, 6);
        renderCards("home-cards", filtered);
      });
    });
  }

  /* ============ PROJECTS LIST ============ */
  function renderProjects() {
    VIEW.innerHTML = tpl("tpl-projects");
    renderCards("proj-cards", window.PROJECTS);

    let currentCat = "all";
    let currentKeyword = "";

    const refresh = () => {
      let list = window.PROJECTS;
      if (currentCat !== "all") list = list.filter((p) => p.category === currentCat);
      if (currentKeyword) {
        const kw = currentKeyword.toLowerCase();
        list = list.filter(
          (p) =>
            p.title.toLowerCase().includes(kw) ||
            p.summary.toLowerCase().includes(kw) ||
            (p.stack || []).some((s) => s.toLowerCase().includes(kw)) ||
            (p.tags || []).some((s) => s.toLowerCase().includes(kw))
        );
      }
      renderCards("proj-cards", list);
    };

    $all("#proj-tabs .tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $all("#proj-tabs .tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        currentCat = tab.dataset.cat;
        refresh();
      });
    });

    const search = document.getElementById("search-box");
    if (search) {
      search.addEventListener("input", (e) => {
        currentKeyword = e.target.value.trim();
        refresh();
      });
    }
  }

  /* ============ CATEGORY ============ */
  function renderCategory(params) {
    const cat = params.cat;
    const meta = window.CATEGORIES[cat];
    VIEW.innerHTML = tpl("tpl-category");
    document.getElementById("cat-title").textContent = meta ? meta.title : "📁 项目分类";
    document.getElementById("cat-desc").textContent = meta ? meta.desc : "";
    const list = window.PROJECTS.filter((p) => p.category === cat);
    renderCards("cat-cards", list);
  }

  /* ============ ROADMAP ============ */
  function renderRoadmap() {
    VIEW.innerHTML = tpl("tpl-roadmap");
  }

  /* ============ ABOUT ============ */
  function renderAbout() {
    VIEW.innerHTML = tpl("tpl-about");
  }

  /* ============ PROJECT DETAIL ============ */
  function renderProjectDetail(params) {
    const project = window.PROJECTS.find((p) => p.id === params.id);
    if (!project) {
      VIEW.innerHTML =
        '<section class="section"><div class="container"><div class="empty">😕 项目不存在</div></div></section>';
      return;
    }

    VIEW.innerHTML = tpl("tpl-project-detail");
    const page = el(`
      <section class="detail">
        <div class="container detail__grid">
          <div class="detail__main">
            <div class="detail__meta">
              <span class="card__cat ${project.catClass}">${project.catLabel}</span>
              <span class="card__cat card__cat--crm">${project.status || ""}</span>
              <span class="card__cat card__cat--prd">${project.date || ""}</span>
            </div>
            <h1>${project.title}</h1>
            <p style="color:var(--ink-muted);margin-bottom:24px;font-size:15px;">${project.summary}</p>

            <div id="md-content" style="margin-top:24px;"></div>
          </div>
          <aside class="detail__side">
            <div class="side__card">
              <div class="side__title">🛠️ 技术栈</div>
              <div style="display:flex;flex-wrap:wrap;gap:6px;">
                ${(project.stack || []).map((s) => `<span style="font-size:12px;background:var(--bg-alt);color:var(--ink-2);padding:3px 10px;border-radius:var(--radius-pill);font-family:SF Mono,Menlo,monospace;">${s}</span>`).join("")}
              </div>
            </div>

            <div class="side__card">
              <div class="side__title">✨ 核心亮点</div>
              <ul class="side__list">
                ${(project.highlights || []).map((h) => `<li>▸ ${h}</li>`).join("")}
              </ul>
            </div>

            <div class="side__card">
              <div class="side__title">📊 量化成果</div>
              ${(project.metrics || []).map(([k, v]) => `
                <div class="side__stat"><span>${k}</span><strong>${v}</strong></div>
              `).join("")}
            </div>

            ${project.liveUrl ? `
              <div class="side__card">
                <a href="${project.liveUrl}" target="_blank" class="side__cta" style="display:block;text-align:center;color:#fff;text-decoration:none;">🔗 查看真实交付物（飞书）</a>
              </div>
            ` : ""}

            <div class="side__card">
              <div class="side__title">📑 相关项目</div>
              <ul class="side__list">
                ${window.PROJECTS.filter((p) => p.id !== project.id && p.category === project.category).slice(0, 3)
                  .map((p) => `<li><a href="#/project/${p.id}">${p.title}</a></li>`).join("") ||
                  `<li><a href="#/projects">查看全部项目 →</a></li>`}
              </ul>
            </div>
          </aside>
        </div>
      </section>
    `);
    VIEW.appendChild(page);

    // 加载 MD 文档
    loadMarkdown(project);
  }

  /* ============ Markdown Loader ============ */
  function loadMarkdown(project) {
    const container = document.getElementById("md-content");
    if (!container || !project.docFile) return;

    container.innerHTML = `
      <div style="padding:60px 20px;text-align:center;color:var(--ink-muted);font-size:14px;background:var(--bg-alt);border-radius:var(--radius-lg);border:2px dashed var(--border);">
        ⏳ 正在加载项目文档...
      </div>
    `;

    fetch("docs/" + project.docFile)
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then((text) => {
        let md = text;
        // 如果指定了只展示某一节
        if (project.docSection) {
          md = extractSection(md, project.docSection);
        }
        // 渲染
        marked.setOptions({ gfm: true, breaks: false });
        let html = marked.parse(md);
        container.innerHTML = `<div class="detail__md">${html}</div>`;
        // 代码高亮
        container.querySelectorAll("pre code").forEach((block) => {
          if (window.hljs) hljs.highlightElement(block);
        });
      })
      .catch((err) => {
        container.innerHTML = `
          <div class="detail__md">
            <blockquote>
              <strong>📄 文档加载中...</strong><br><br>
              这是「${project.title}」的完整实现文档，包含：<br>
              • 架构图 / 对接链路图<br>
              • 核心代码（Python / Node.js）<br>
              • 飞书 / 销售易 MCP 配置清单<br>
              • 量化成果 / 简历 STAR 描述<br><br>
              👉 请先启动本地 HTTP Server（<code>python3 -m http.server</code>）再访问本页，MD 会自动加载渲染。
            </blockquote>
          </div>
        `;
        console.warn("MD load failed:", err);
      });
  }

  /**
   * 从大 md 中抽取指定章节（按二级/三级标题匹配）
   * 输入: "# 🚀 项目 1：LTC 销售话术...\n\n..."
   * 输出: 该节内容直到下一个同级或更高级标题
   */
  function extractSection(md, heading) {
    const lines = md.split("\n");
    let startIdx = -1;
    const headingLevel = (heading.match(/^#+/) || ["##"])[0].length;
    // 简单匹配：标题包含 heading 内容（忽略 emoji/标点差异）
    const norm = (s) => s.replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, "").toLowerCase();
    const key = norm(heading);

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (/^#{1,6}\s/.test(line)) {
        const text = line.replace(/^#+\s*/, "");
        if (norm(text).includes(key) || key.includes(norm(text))) {
          startIdx = i;
          break;
        }
      }
    }
    if (startIdx === -1) return md; // 没找到就返回全文

    let endIdx = lines.length;
    const startLevel = (lines[startIdx].match(/^#+/)[0]).length;
    for (let i = startIdx + 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (/^#{1,6}\s/.test(line)) {
        const level = (line.match(/^#+/)[0]).length;
        if (level <= startLevel) {
          endIdx = i;
          break;
        }
      }
    }
    return lines.slice(startIdx, endIdx).join("\n");
  }
})();
