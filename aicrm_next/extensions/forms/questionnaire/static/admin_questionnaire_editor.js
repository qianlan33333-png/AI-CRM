const editorConfigElement = document.getElementById('questionnaire-editor-config');
if (!editorConfigElement) throw new Error('questionnaire editor config is missing');
const editorConfig = JSON.parse(editorConfigElement.textContent || '{}');
const listEl = document.getElementById('questionnaire-list');
const listSearchEl = document.getElementById('list-search');
const statusFilterEl = document.getElementById('status-filter');
const previewHeadEl = document.getElementById('preview-head');
const previewQuestionsEl = document.getElementById('preview-questions');
const previewRulesWrapEl = document.getElementById('preview-rules-wrap');
const inspectorBodyEl = document.getElementById('inspector-body');
const inspectorTitleEl = document.getElementById('inspector-title');
const inspectorSubtitleEl = document.getElementById('inspector-subtitle');
const backLinkEl = document.getElementById('back-link');
const editorPageTitleEl = document.getElementById('editor-page-title');
const editorPageSubtitleEl = document.getElementById('editor-page-subtitle');
const editorSecondaryActionsEl = document.getElementById('editor-secondary-actions');
const topbarTitleEl = document.getElementById('topbar-title');
const draftIndicatorEl = document.getElementById('draft-indicator');
const tagCatalogMessageEl = document.getElementById('tag-catalog-message');
const drawerOverlayEl = document.getElementById('drawer-overlay');
const drawerTitleEl = document.getElementById('drawer-title');
const drawerBodyEl = document.getElementById('drawer-body');
const toastEl = document.getElementById('toast');

const state = {
  list: [],
  availableTags: [],
  availableTagMap: new Map(),
  questionnaire: null,
  currentId: null,
  editorMode: editorConfig.mode || 'new',
  selection: { kind: 'questionnaire' },
  ruleMode: false,
  lastRuleKey: '',
  assessmentStep: 'basic',
  assessmentResultTab: 'dimension',
  assessmentPreviewMode: 'full',
  selectedDimensionKey: '',
  selectedQuestionKey: '',
  selectedAssessmentTypeKey: '',
  selectedOverallLevelKey: '',
  initialSnapshot: '',
  persistedIsDisabled: false,
  listSearch: '',
  statusFilter: 'all',
  loadingList: false,
  tagModal: {
    open: false,
    search: '',
    selected: [],
    target: null,
  },
};
let localSeq = 0;
let toastTimer = null;
const DEFAULT_ASSESSMENT_TEMPLATE_ID = 'siyuan_ip_business';
const DEFAULT_ASSESSMENT_TEMPLATE_NAME = '小 IP 商业力测评';
const SIDEBAR_PROFILE_FIELD_OPTIONS = [
  { value: '', label: '无，不映射到侧边栏' },
  { value: 'source', label: '用户来源' },
  { value: 'industry', label: '行业信息' },
  { value: 'industry_description', label: '行业具体描述' },
  { value: 'needs_blockers_followup', label: '需求、卡点、跟进状态' },
];

function nextLocalKey(prefix) {
  localSeq += 1;
  return `${prefix}_${Date.now()}_${localSeq}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeTagIds(value) {
  if (Array.isArray(value)) {
    return [...new Set(value.map((item) => String(item || '').trim()).filter(Boolean))];
  }
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return normalizeTagIds(parsed);
    } catch (error) {
      return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))];
    }
  }
  return [];
}

function parseManualTagInput(value) {
  if (!String(value || '').trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? normalizeTagIds(parsed) : [];
  } catch (error) {
    return normalizeTagIds(value);
  }
}

function formatQuestionType(type) {
  if (type === 'single_choice') return '单选题';
  if (type === 'multi_choice') return '多选题';
  if (type === 'textarea') return '文本题';
  if (type === 'mobile') return '手机号题';
  return type || '题目';
}

function normalizeSidebarProfileField(value) {
  const normalized = String(value || '').trim();
  return SIDEBAR_PROFILE_FIELD_OPTIONS.some((item) => item.value === normalized) ? normalized : '';
}

function sidebarProfileFieldLabel(value) {
  const normalized = normalizeSidebarProfileField(value);
  if (!normalized) return '';
  return SIDEBAR_PROFILE_FIELD_OPTIONS.find((item) => item.value === normalized)?.label || '';
}

function sidebarProfileFieldOptionsHtml(value) {
  const normalized = normalizeSidebarProfileField(value);
  return SIDEBAR_PROFILE_FIELD_OPTIONS.map((item) => `
    <option value="${escapeHtml(item.value)}" ${item.value === normalized ? 'selected' : ''}>${escapeHtml(item.label)}</option>
  `).join('');
}

function sidebarProfileChipHtml(question) {
  const label = sidebarProfileFieldLabel(question.sidebar_profile_field);
  return label ? `<span class="profile-map-chip">${escapeHtml(label)}</span>` : '';
}

function buildDefaultAssessmentConfig() {
  return {
    total_score_title: '多维测评综合结果',
    strength_count: 2,
    weakness_count: 2,
    overall_levels: [
      { min_score: 0, max_score: 40, title: '待破局型', summary: '基础动作还不稳定，优先补齐获客、承接和成交闭环。' },
      { min_score: 41, max_score: 70, title: '成长期', summary: '已经有可复用动作，下一步把优势项标准化，把短板项做成固定流程。' },
      { min_score: 71, max_score: 100, title: '放大型', summary: '整体链路比较完整，适合加大内容分发、社群运营和转化承接投入。' },
    ],
    dimensions: [
      {
        key: 'traffic',
        name: '引流能力',
        type_priority: ['stable', 'content', 'passive'],
        levels: [
          { min_score: 0, max_score: 8, title: '薄弱' },
          { min_score: 9, max_score: 16, title: '可用' },
          { min_score: 17, max_score: 20, title: '优势' },
        ],
        types: [
          { key: 'passive', name: '被动型', summary: '主要依赖熟人、转介绍或偶发曝光。' },
          { key: 'content', name: '内容型', summary: '能通过内容稳定吸引一部分潜在客户。' },
          { key: 'stable', name: '稳定型', summary: '有相对固定的获客渠道和动作节奏。' },
        ],
      },
      {
        key: 'trust',
        name: '信任建立',
        type_priority: ['system', 'case', 'intuition'],
        levels: [
          { min_score: 0, max_score: 8, title: '薄弱' },
          { min_score: 9, max_score: 16, title: '可用' },
          { min_score: 17, max_score: 20, title: '优势' },
        ],
        types: [
          { key: 'intuition', name: '直觉型', summary: '更多靠个人表达和临场沟通建立信任。' },
          { key: 'case', name: '案例型', summary: '会用案例、成果和过程材料增强可信度。' },
          { key: 'system', name: '系统型', summary: '有清晰的信任素材和持续触达机制。' },
        ],
      },
      {
        key: 'offer',
        name: '产品设计',
        type_priority: ['ladder', 'single', 'custom'],
        levels: [
          { min_score: 0, max_score: 8, title: '薄弱' },
          { min_score: 9, max_score: 16, title: '可用' },
          { min_score: 17, max_score: 20, title: '优势' },
        ],
        types: [
          { key: 'custom', name: '定制型', summary: '交付依赖个人经验和临时定制。' },
          { key: 'single', name: '单品型', summary: '有明确主产品，但产品阶梯还不完整。' },
          { key: 'ladder', name: '阶梯型', summary: '有从引流到成交的产品梯度。' },
        ],
      },
      {
        key: 'conversion',
        name: '成交能力',
        type_priority: ['active', 'script', 'passive'],
        levels: [
          { min_score: 0, max_score: 8, title: '薄弱' },
          { min_score: 9, max_score: 16, title: '可用' },
          { min_score: 17, max_score: 20, title: '优势' },
        ],
        types: [
          { key: 'passive', name: '被动型', summary: '用户咨询后才推进，缺少主动成交动作。' },
          { key: 'script', name: '话术型', summary: '有基本销售话术，但节奏和跟进还需要稳定。' },
          { key: 'active', name: '主动型', summary: '能主动筛选、邀约、跟进并推动成交。' },
        ],
      },
      {
        key: 'operation',
        name: '运营承接',
        type_priority: ['loop', 'manual', 'loose'],
        levels: [
          { min_score: 0, max_score: 8, title: '薄弱' },
          { min_score: 9, max_score: 16, title: '可用' },
          { min_score: 17, max_score: 20, title: '优势' },
        ],
        types: [
          { key: 'loose', name: '松散型', summary: '触达和跟进比较随机，容易丢线索。' },
          { key: 'manual', name: '人工型', summary: '靠人工记录和提醒维持运营动作。' },
          { key: 'loop', name: '闭环型', summary: '线索、标签、跟进和复盘形成了闭环。' },
        ],
      },
    ],
    recommendations: [
      { dimension_key: 'conversion', max_score: 8, title: '优先补成交动作', summary: '先把邀约、咨询、跟进和成交判断标准固定下来。' },
      { dimension_key: 'traffic', max_score: 8, title: '优先补获客入口', summary: '选择 1-2 个稳定渠道，建立固定内容或活动节奏。' },
    ],
  };
}

function buildSiyuanIpAssessmentPreset() {
  const dimensions = [
    { key: '用户获取', full: '用户获取（流量引入能力）', cta: '流量诊断 + 钩子设计轻咨询', priority: ['磁铁型', '钩子型', '莽撞型', '佛系型'] },
    { key: '用户维护', full: '用户维护（关系升温能力）', cta: '朋友圈剧本 / 私域 SOP 工具包', priority: ['养鱼型', '暖男/女型', '心急型', '僵尸粉型'] },
    { key: '用户成交', full: '用户成交（临门一脚能力）', cta: '成交话术 SOP + 案例库', priority: ['催眠型', '推销型', '压迫型', '被动型'] },
    { key: '用户交付', full: '用户交付（预期管理能力）', cta: '交付仪式 + 超预期设计模板', priority: ['惊喜型', '打工型', '割韭菜型', '放羊型'] },
    { key: '用户裂变', full: '用户裂变 / 升单（价值挖掘能力）', cta: '老客户运营 + 产品阶梯设计', priority: ['共生型', '利诱型', '随缘型', '一锤子型'] },
  ];
  const questions = [
    ['用户获取', '你想给自己加 100 个新精准粉，第一反应是？', [['佛系型', 1, '朋友圈吼一嗓子“求介绍”，等天上掉馅饼'], ['莽撞型', 2, '进十几个群拉人，能加多少加多少，先有量再说'], ['钩子型', 3, '设计个钩子，定向丢到目标群里钓鱼'], ['磁铁型', 4, '写一篇戳痛点的内容发到小红书 / 视频号，等他们主动来加']]],
    ['用户获取', '你目前主要的引流方式是？', [['佛系型', 1, '老客户介绍 / 朋友帮忙拉，听天由命'], ['莽撞型', 2, '不停加人 / 进群拉人，量大概率高'], ['钩子型', 3, '几个固定的钩子和引流活动'], ['磁铁型', 4, '内容矩阵让人主动来']]],
    ['用户获取', '你的引流钩子长什么样？', [['佛系型', 1, '没有固定钩子，临时想啥发啥'], ['莽撞型', 2, '进群送资料、加我送 XXX 这类无差别钩子'], ['钩子型', 3, '针对特定问题的解决方案'], ['磁铁型', 4, '通过持续内容 / 人设吸引']]],
    ['用户获取', '看到同行 IP 涨粉很快，你的反应通常是？', [['佛系型', 1, '羡慕一下，觉得人家命好'], ['莽撞型', 2, '去他的评论区 / 粉丝群里加人'], ['钩子型', 3, '拆解他的钩子并照着设计一个'], ['磁铁型', 4, '研究人设和定位，思考自己的差异化']]],
    ['用户获取', '如果有 1000 块预算专门用来涨粉，你会怎么花？', [['佛系型', 1, '不舍得花，宁可自然涨'], ['莽撞型', 2, '全部砸到投流上'], ['钩子型', 3, '设计引流活动，钱花在钩子物料和投放上'], ['磁铁型', 4, '投在内容创作上']]],
    ['用户维护', '一个新朋友刚加你微信、备注好了，你下一步会做什么？', [['僵尸粉型', 1, '先放着，等他自己来找我说话'], ['心急型', 2, '立刻甩一份产品介绍 / 价目表'], ['暖男/女型', 3, '简单聊几句，问问他做什么的'], ['养鱼型', 4, '有破冰节奏：自我介绍 + 小礼物 + 选择题']]],
    ['用户维护', '翻一下你最近一周的朋友圈，主要内容是？', [['僵尸粉型', 1, '有空才发，没空就空着'], ['心急型', 2, '几乎全是产品广告 / 带货链接'], ['暖男/女型', 3, '一半生活一半干货'], ['养鱼型', 4, '每条都有定位，按节奏发']]],
    ['用户维护', '让你立刻说出最赚钱的 10 个客户是谁，你的反应是？', [['僵尸粉型', 1, '没想过这个问题'], ['心急型', 2, '大概有印象，但说不出共同点'], ['暖男/女型', 3, '能说出名字和大概特征'], ['养鱼型', 4, '能打开表格 / 笔记说清楚']]],
    ['用户维护', '你上一次主动联系老用户，不是为了卖东西，是什么时候？', [['僵尸粉型', 1, '想不起来了'], ['心急型', 2, '上次卖东西时顺便聊了两句'], ['暖男/女型', 3, '节日发过群祝福'], ['养鱼型', 4, '这周内给他发了有用内容 / 资料']]],
    ['用户维护', '用户和你的日常互动是？', [['僵尸粉型', 1, '几乎零互动'], ['心急型', 2, '偶尔点赞我的促销动态'], ['暖男/女型', 3, '经常点赞评论，关系还不错'], ['养鱼型', 4, '主动期待更新，会私信问问题']]],
    ['用户成交', '用户问完产品细节后说“我再考虑一下”，你的第一反应是？', [['被动型', 1, '好的，您慢慢考虑'], ['压迫型', 2, '今天最后一天优惠，反复催'], ['推销型', 3, '再次强调产品好处和性价比'], ['催眠型', 4, '先问他主要纠结哪一点']]],
    ['用户成交', '卖一个 999 元的产品，你的成交话术大概是？', [['被动型', 1, '没有固定话术，对方问什么答什么'], ['压迫型', 2, '限时优惠、错过没了、倒计时'], ['推销型', 3, '产品有三大好处，性价比高'], ['催眠型', 4, '用案例和故事让用户自己看到需求']]],
    ['用户成交', '用户犹豫“有点贵”时，你最常用的应对是？', [['被动型', 1, '那您再考虑'], ['压迫型', 2, '今天真的最后一天'], ['推销型', 3, '帮他算账，分摊到每天很便宜'], ['催眠型', 4, '先确认是预算问题还是时机问题']]],
    ['用户成交', '当你成交了一单，你最常做的复盘是？', [['被动型', 1, '几乎不复盘'], ['压迫型', 2, '看有没有给足紧迫感'], ['推销型', 3, '看哪个卖点 / 价格打动他'], ['催眠型', 4, '拆解购买动机和流失节点']]],
    ['用户成交', '你最相信下面哪句话？', [['被动型', 1, '缘分到了自然就成'], ['压迫型', 2, '用户都是被推一把才动的'], ['推销型', 3, '价值讲透，用户就会买'], ['催眠型', 4, '用户买的是产品解决的问题']]],
    ['用户交付', '用户付款的那一刻，你给他发的第一条消息通常是？', [['放羊型', 1, '没发，等他自己来问'], ['割韭菜型', 2, '感谢支持，然后就消失'], ['打工型', 3, '感谢购买，稍后安排服务'], ['惊喜型', 4, '欢迎仪式：感谢 + 下一步明细 + 小惊喜']]],
    ['用户交付', '服务开始前，你会怎么让用户清楚会得到什么？', [['放羊型', 1, '不会刻意对齐'], ['割韭菜型', 2, '简单说一句包含 ABC'], ['打工型', 3, '发服务清单 / 流程图'], ['惊喜型', 4, '一对一确认时间、阶段成果和不包含项']]],
    ['用户交付', '服务过程中用户说效果没达到预期，你怎么处理？', [['放羊型', 1, '让他自己消化'], ['割韭菜型', 2, '简单解释，能搪塞过去就好'], ['打工型', 3, '认真听反馈，调整服务内容'], ['惊喜型', 4, '共情，拆解 gap，并给补救方案']]],
    ['用户交付', '服务结束后的最后一次互动，通常是？', [['放羊型', 1, '服务一结束就各回各家'], ['割韭菜型', 2, '感谢支持，再见'], ['打工型', 3, '发反馈问卷'], ['惊喜型', 4, '结案仪式，顺势铺垫下一阶段']]],
    ['用户交付', '用户反馈中，最让你紧张的是哪句话？', [['放羊型', 1, '你怎么从来不主动联系我'], ['割韭菜型', 2, '你收钱前后态度不一样'], ['打工型', 3, '没毛病，但没记忆点'], ['惊喜型', 4, '你比我预期的还用心']]],
    ['用户裂变', '老用户买完一次后，你的设计里他下一步会做什么？', [['一锤子型', 1, '没设计，看他自己'], ['随缘型', 2, '期待他自己再来买'], ['利诱型', 3, '发优惠券 / 回访求他再来'], ['共生型', 4, '有清晰产品阶梯，自然想买进阶款']]],
    ['用户裂变', '你对老用户会有专门的优惠方案吗？', [['一锤子型', 1, '买了就是买了，不分新老'], ['随缘型', 2, '看关系，关系好的便宜点'], ['利诱型', 3, '偶尔做折扣 / 返现活动'], ['共生型', 4, '有明确新老客价格体系和增值方案']]],
    ['用户裂变', '上个月有几个老用户主动给你介绍了新客户？', [['一锤子型', 1, '一个都没有'], ['随缘型', 2, '1-2 个，凭朋友帮忙'], ['利诱型', 3, '5 个左右，因为有红包 / 礼品'], ['共生型', 4, '10 个以上，老用户主动安利']]],
    ['用户裂变', '你的产品阶梯 / 客单价分布是？', [['一锤子型', 1, '只有一个产品 / 客单价'], ['随缘型', 2, '有几个产品但没明确升单逻辑'], ['利诱型', 3, '有阶梯，但靠优惠券推进'], ['共生型', 4, '完整产品阶梯，自然进阶到高客单']]],
    ['用户裂变', '你对老用户的看法，更接近哪种？', [['一锤子型', 1, '老客户太磨人，不如开发新客'], ['随缘型', 2, '老客户重要，但没时间维护'], ['利诱型', 3, '会做专属动作或返现激励'], ['共生型', 4, '老客户是核心资产']]],
  ];
  const dimensionFeedback = {
    '用户获取': {
      levels: [
        ['待破局', '现在主要靠偶发曝光或熟人介绍，获客动作不稳定。先固定一个目标人群和一个引流入口。'],
        ['入门', '已经有一些获客动作，但钩子和渠道还不够稳定。建议把最有效的动作重复跑 2-3 周。'],
        ['进阶', '有明确获客方法，能持续带来线索。下一步重点是提升精准度和承接效率。'],
        ['专家', '获客入口比较稳定，用户会被内容、人设或活动主动吸引。可以开始做渠道放大和内容矩阵。'],
      ],
      types: {
        '磁铁型': '用户更容易被你的内容、人设或观点主动吸引，适合持续放大内容资产和个人定位。',
        '钩子型': '你会用资料、活动或明确利益点吸引用户，适合继续打磨钩子和投放场景。',
        '莽撞型': '你愿意主动出击，但容易靠加人、拉群、投放堆数量，需要补筛选和承接逻辑。',
        '佛系型': '获客更多依赖熟人、自然曝光或运气，短期需要先建立固定的引流动作。',
      },
      recommendation: '先选定一个主渠道，设计一个能筛出目标用户的钩子，并连续复盘来源、加粉成本和有效咨询数。',
    },
    '用户维护': {
      levels: [
        ['待破局', '加来的用户容易沉默，关系维护没有固定动作。先补新好友破冰和老用户触达。'],
        ['入门', '会做一些互动和朋友圈维护，但节奏不稳定。建议沉淀固定的内容和私聊 SOP。'],
        ['进阶', '关系经营已有方法，用户愿意互动。下一步可以按用户阶段做分层维护。'],
        ['专家', '用户关系资产化程度较高，能持续获得反馈、复购和转介绍线索。适合导入自动化标签和提醒。'],
      ],
      types: {
        '养鱼型': '你会有节奏地养熟关系，适合做用户分层、朋友圈剧本和周期性触达。',
        '暖男/女型': '你愿意真诚互动，但还比较依赖个人状态，需要把互动动作标准化。',
        '心急型': '你容易过早进入销售，用户还没建立信任就被推产品，需要先补价值和信任素材。',
        '僵尸粉型': '加到用户后缺少持续触达，用户容易变成沉默好友，需要先建立最小维护节奏。',
      },
      recommendation: '建立“新好友 3 步破冰 + 7 天价值触达 + 老用户月度回访”的最小维护 SOP。',
    },
    '用户成交': {
      levels: [
        ['待破局', '成交主要靠用户主动问和临场发挥，缺少推进动作。先补咨询、异议和跟进话术。'],
        ['入门', '能讲清产品价值，但成交节奏不够稳。建议把常见异议和案例统一整理。'],
        ['进阶', '有比较完整的成交动作，能主动推进。下一步重点是提高成交率和客单价。'],
        ['专家', '能基于需求、案例和时机推动成交，成交链路成熟。适合做标准化话术和团队复用。'],
      ],
      types: {
        '催眠型': '你能围绕用户需求、故事和案例让用户自己看到购买理由，是比较成熟的成交方式。',
        '推销型': '你擅长讲卖点和性价比，但需要更多追问用户真实阻碍，避免只是在介绍产品。',
        '压迫型': '你常用限时、优惠、倒计时推进，短期有效但容易损伤信任，需要补价值确认。',
        '被动型': '你习惯等用户自己决定，缺少主动跟进和异议处理，容易丢掉本可成交的线索。',
      },
      recommendation: '整理 10 个真实成交案例，配套“需求确认、异议处理、跟进提醒、成交复盘”四段动作。',
    },
    '用户交付': {
      levels: [
        ['待破局', '交付缺少主动管理，用户容易不清楚下一步。先补付款后的欢迎和流程说明。'],
        ['入门', '能完成服务，但过程缺少记忆点和预期管理。建议补节点提醒和阶段反馈。'],
        ['进阶', '交付过程较稳定，用户体验可控。下一步可以加入超预期动作和复购铺垫。'],
        ['专家', '交付有仪式、有反馈、有结案，用户能感受到专业度。适合沉淀交付模板和案例素材。'],
      ],
      types: {
        '惊喜型': '你重视交付体验和超预期设计，用户更容易产生信任、复购和主动推荐。',
        '打工型': '你能认真完成服务，但体验偏功能性，需要增加节点感、结果感和案例沉淀。',
        '割韭菜型': '用户可能感到收款前后落差，需要补齐服务承诺、过程反馈和售后说明。',
        '放羊型': '交付中主动联系少、规则不清楚，用户容易焦虑，需要先建立基础交付流程。',
      },
      recommendation: '设置“付款欢迎、服务前对齐、服务中反馈、结案总结”四个固定交付节点。',
    },
    '用户裂变': {
      levels: [
        ['待破局', '老用户价值没有被持续经营，复购和转介绍主要靠运气。先补老用户回访和下一步产品。'],
        ['入门', '知道老用户重要，但缺少固定升单和转介绍动作。建议先设计一个老用户专属机制。'],
        ['进阶', '已有复购或转介绍动作，能从老用户中获得增长。下一步是产品阶梯和权益体系。'],
        ['专家', '老用户已经成为核心资产，能自然带来复购、升单和推荐。适合做会员制或高阶产品。'],
      ],
      types: {
        '共生型': '你把老用户当长期资产经营，产品阶梯和关系维护能自然带来复购与推荐。',
        '利诱型': '你会用优惠、返现或礼品刺激复购转介绍，但需要补价值驱动和长期机制。',
        '随缘型': '你认可老用户重要，但没有固定动作，复购和推荐主要靠关系或运气。',
        '一锤子型': '你更重视首单成交，老用户经营不足，需要先设计一个可承接的下一步产品。',
      },
      recommendation: '为老用户设计“回访问题、专属权益、转介绍理由、进阶产品”四件套。',
    },
  };
  return {
    name: '小 IP 商业力测评',
    title: '小 IP 商业力测评',
    description: '评估用户在用户获取、用户维护、用户成交、用户交付、用户裂变上的当前状态，并给出可运营的标签和建议。',
    dimensions,
    questions,
    assessment_config: {
      template_id: DEFAULT_ASSESSMENT_TEMPLATE_ID,
      template_name: DEFAULT_ASSESSMENT_TEMPLATE_NAME,
      total_score_title: '小 IP 商业力测评结果',
      strength_count: 2,
      weakness_count: 2,
      overall_levels: [
        { min_score: 0, max_score: 44, title: '待破局', summary: '当前商业闭环还比较散，最优先的不是做复杂运营，而是先让获客、维护、成交、交付和复购各有一个能跑起来的基础动作。' },
        { min_score: 45, max_score: 64, title: '入门', summary: '你已经有一些有效动作，但更多依赖个人状态和临场发挥。下一步要把高频动作标准化，先解决最弱的 1-2 个维度。' },
        { min_score: 65, max_score: 84, title: '进阶', summary: '整体链路已经比较完整，部分维度能稳定产生结果。建议优先放大优势项，同时把短板维度做成固定流程。' },
        { min_score: 85, max_score: 100, title: '专家', summary: '你已经具备成熟的小 IP 商业闭环，适合把方法论产品化、自动化，并通过内容、老用户和高客单产品继续放大。' },
      ],
      dimensions: dimensions.map((dimension) => ({
        key: dimension.key,
        name: dimension.key,
        summary: dimension.full,
        type_priority: dimension.priority,
        types: dimension.priority.map((name) => ({
          key: name,
          name,
          summary: dimensionFeedback[dimension.key]?.types?.[name] || `${name}代表用户在“${dimension.key}”上的主要行为偏好。`,
        })),
        levels: (dimensionFeedback[dimension.key]?.levels || []).map(([title, summary], index) => ({
          min_score: [0, 9, 13, 17][index],
          max_score: [8, 12, 16, 20][index],
          title,
          summary,
        })),
      })),
      recommendations: dimensions.map((dimension) => ({
        dimension_key: dimension.key,
        max_score: 12,
        title: `优先优化${dimension.key}`,
        summary: dimensionFeedback[dimension.key]?.recommendation || dimension.cta,
      })),
    },
  };
}

function normalizeAssessmentConfig(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value;
}

function createAssessmentType(type = {}, index = 0) {
  return {
    local_key: type.local_key || nextLocalKey('assessment_type'),
    key: String(type.key || type.name || `type_${index + 1}`).trim() || `type_${index + 1}`,
    name: String(type.name || `类型 ${index + 1}`).trim() || `类型 ${index + 1}`,
    title: String(type.title || type.name || `类型 ${index + 1}`).trim() || `类型 ${index + 1}`,
    greeting: String(type.greeting || '').trim(),
    summary: String(type.summary || '').trim(),
    diagnosis: String(type.diagnosis || type.summary || type.description || '').trim(),
    problem_hint: String(type.problem_hint || '').trim(),
    recommended_action: String(type.recommended_action || '').trim(),
    course_name: String(type.course_name || '').trim(),
    course_url: String(type.course_url || type.cta_url || '').trim(),
    cta_text: String(type.cta_text || '').trim(),
    enabled: type.enabled === undefined ? true : Boolean(type.enabled),
    show_in_result: type.show_in_result === undefined ? true : Boolean(type.show_in_result),
    sort_order: type.sort_order ?? (index + 1),
    tag_codes: normalizeTagIds(type.tag_codes || []),
  };
}

function createAssessmentLevel(level = {}, index = 0) {
  return {
    local_key: level.local_key || nextLocalKey('assessment_level'),
    min_score: level.min_score ?? '',
    max_score: level.max_score ?? '',
    title: String(level.title || `分层 ${index + 1}`).trim() || `分层 ${index + 1}`,
    greeting: String(level.greeting || '').trim(),
    summary: String(level.summary || level.diagnosis || '').trim(),
    recommended_action: String(level.recommended_action || '').trim(),
    course_name: String(level.course_name || '').trim(),
    course_url: String(level.course_url || level.cta_url || '').trim(),
    cta_text: String(level.cta_text || '').trim(),
    enabled: level.enabled === undefined ? true : Boolean(level.enabled),
    sort_order: level.sort_order ?? (index + 1),
    tag_codes: normalizeTagIds(level.tag_codes || []),
  };
}

function createAssessmentDimension(dimension = {}, index = 0) {
  const rawTypes = Array.isArray(dimension.types) ? dimension.types : [];
  const types = rawTypes.length
    ? rawTypes.map((item, itemIndex) => createAssessmentType(item, itemIndex))
    : [
        createAssessmentType({ key: `type_${index + 1}_1`, name: '类型 A' }, 0),
        createAssessmentType({ key: `type_${index + 1}_2`, name: '类型 B' }, 1),
      ];
  const priority = Array.isArray(dimension.type_priority)
    ? dimension.type_priority.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
  const sortedTypes = priority.length
    ? [
        ...priority.map((key) => types.find((item) => item.key === key)).filter(Boolean),
        ...types.filter((item) => !priority.includes(item.key)),
      ]
    : types;
  const rawLevels = Array.isArray(dimension.levels) ? dimension.levels : [];
  return {
    local_key: dimension.local_key || nextLocalKey('assessment_dimension'),
    key: String(dimension.key || `dimension_${index + 1}`).trim() || `dimension_${index + 1}`,
    name: String(dimension.name || `维度 ${index + 1}`).trim() || `维度 ${index + 1}`,
    summary: String(dimension.summary || '').trim(),
    weight: dimension.weight ?? '',
    scoring_method: String(dimension.scoring_method || 'sum').trim() || 'sum',
    category_method: String(dimension.category_method || 'most_selected').trim() || 'most_selected',
    enabled: dimension.enabled === undefined ? true : Boolean(dimension.enabled),
    participates_in_total_score: dimension.participates_in_total_score === undefined ? true : Boolean(dimension.participates_in_total_score),
    show_in_result: dimension.show_in_result === undefined ? true : Boolean(dimension.show_in_result),
    sort_order: dimension.sort_order ?? (index + 1),
    types: sortedTypes,
    levels: rawLevels.length
      ? rawLevels.map((item, itemIndex) => createAssessmentLevel(item, itemIndex))
      : [
          createAssessmentLevel({ min_score: 0, max_score: 8, title: '薄弱' }, 0),
          createAssessmentLevel({ min_score: 9, max_score: 16, title: '可用' }, 1),
          createAssessmentLevel({ min_score: 17, max_score: 20, title: '优势' }, 2),
        ],
  };
}

function createAssessmentBuilderFromConfig(config = {}) {
  const normalized = normalizeAssessmentConfig(config);
  const rawDimensions = Array.isArray(normalized.dimensions) ? normalized.dimensions : [];
  const rawOverallLevels = Array.isArray(normalized.overall_levels) ? normalized.overall_levels : [];
  return {
    template_id: String(normalized.template_id || '').trim(),
    template_name: String(normalized.template_name || '').trim(),
    asset_kind: String(normalized.asset_kind || '').trim(),
    source_questionnaire_id: normalized.source_questionnaire_id ?? '',
    total_score_title: String(normalized.total_score_title || '多维测评综合结果').trim() || '多维测评综合结果',
    strength_count: Number(normalized.strength_count || 2),
    weakness_count: Number(normalized.weakness_count || 2),
    dimensions: rawDimensions.map((item, index) => createAssessmentDimension(item, index)),
    overall_levels: rawOverallLevels.length
      ? rawOverallLevels.map((item, index) => createAssessmentLevel(item, index))
      : [
          createAssessmentLevel({ min_score: 0, max_score: 40, title: '待提升' }, 0),
          createAssessmentLevel({ min_score: 41, max_score: 70, title: '成长中' }, 1),
          createAssessmentLevel({ min_score: 71, max_score: 100, title: '优势明显' }, 2),
        ],
    recommendations: Array.isArray(normalized.recommendations) ? normalized.recommendations : [],
    final_recommendation: normalized.final_recommendation && typeof normalized.final_recommendation === 'object'
      ? {
          enabled: Boolean(normalized.final_recommendation.enabled),
          title: String(normalized.final_recommendation.title || '').trim(),
          description: String(normalized.final_recommendation.description || '').trim(),
          course_name: String(normalized.final_recommendation.course_name || '').trim(),
          course_url: String(normalized.final_recommendation.course_url || '').trim(),
          cta_text: String(normalized.final_recommendation.cta_text || '').trim(),
        }
      : {
          enabled: false,
          title: '下一步建议',
          description: '',
          course_name: '',
          course_url: '',
          cta_text: '查看完整课程',
        },
  };
}

function buildAssessmentConfigFromBuilder(builder = {}) {
  const dimensions = Array.isArray(builder.dimensions) ? builder.dimensions : [];
  if (!dimensions.length) return {};
  const config = {
    template_id: builder.template_id || '',
    template_name: builder.template_name || '',
    total_score_title: builder.total_score_title || '多维测评综合结果',
    strength_count: Number(builder.strength_count || 2),
    weakness_count: Number(builder.weakness_count || 2),
    overall_levels: (builder.overall_levels || []).map((level, index) => ({
      min_score: level.min_score === '' ? null : Number(level.min_score),
      max_score: level.max_score === '' ? null : Number(level.max_score),
      title: level.title || `分层 ${index + 1}`,
      greeting: level.greeting || '',
      summary: level.summary || '',
      recommended_action: level.recommended_action || '',
      course_name: level.course_name || '',
      course_url: level.course_url || '',
      cta_text: level.cta_text || '',
      enabled: level.enabled !== false,
      sort_order: Number(level.sort_order || (index + 1)),
      tag_codes: normalizeTagIds(level.tag_codes),
    })),
    dimensions: dimensions.map((dimension, index) => ({
      key: dimension.key || `dimension_${index + 1}`,
      name: dimension.name || `维度 ${index + 1}`,
      summary: dimension.summary || '',
      weight: dimension.weight === '' ? null : Number(dimension.weight || 0),
      scoring_method: dimension.scoring_method || 'sum',
      category_method: dimension.category_method || 'most_selected',
      enabled: dimension.enabled !== false,
      participates_in_total_score: dimension.participates_in_total_score !== false,
      show_in_result: dimension.show_in_result !== false,
      sort_order: Number(dimension.sort_order || (index + 1)),
      type_priority: (dimension.types || []).map((type) => type.key),
      types: (dimension.types || []).map((type, typeIndex) => ({
        key: type.key || `type_${index + 1}_${typeIndex + 1}`,
        name: type.name || `类型 ${typeIndex + 1}`,
        title: type.title || type.name || `类型 ${typeIndex + 1}`,
        greeting: type.greeting || '',
        summary: type.summary || '',
        diagnosis: type.diagnosis || type.summary || '',
        problem_hint: type.problem_hint || '',
        recommended_action: type.recommended_action || '',
        course_name: type.course_name || '',
        course_url: type.course_url || '',
        cta_text: type.cta_text || '',
        enabled: type.enabled !== false,
        show_in_result: type.show_in_result !== false,
        sort_order: Number(type.sort_order || (typeIndex + 1)),
        tag_codes: normalizeTagIds(type.tag_codes),
      })),
      levels: (dimension.levels || []).map((level, levelIndex) => ({
        min_score: level.min_score === '' ? null : Number(level.min_score),
        max_score: level.max_score === '' ? null : Number(level.max_score),
        title: level.title || `分层 ${levelIndex + 1}`,
        greeting: level.greeting || '',
        summary: level.summary || '',
        recommended_action: level.recommended_action || '',
        course_name: level.course_name || '',
        course_url: level.course_url || '',
        cta_text: level.cta_text || '',
        enabled: level.enabled !== false,
        sort_order: Number(level.sort_order || (levelIndex + 1)),
        tag_codes: normalizeTagIds(level.tag_codes),
      })),
    })),
    recommendations: builder.recommendations || [],
    final_recommendation: {
      enabled: Boolean(builder.final_recommendation?.enabled),
      title: builder.final_recommendation?.title || '',
      description: builder.final_recommendation?.description || '',
      course_name: builder.final_recommendation?.course_name || '',
      course_url: builder.final_recommendation?.course_url || '',
      cta_text: builder.final_recommendation?.cta_text || '',
    },
  };
  const assetKind = String(builder.asset_kind || '').trim();
  if (assetKind) {
    config.asset_kind = assetKind;
  }
  if (builder.source_questionnaire_id !== undefined && builder.source_questionnaire_id !== '') {
    config.source_questionnaire_id = builder.source_questionnaire_id;
  }
  return config;
}

function ensureAssessmentBuilder() {
  if (!state.questionnaire.assessment_builder) {
    state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(state.questionnaire.assessment_config || {});
  }
  return state.questionnaire.assessment_builder;
}

function assessmentDimensions() {
  return ensureAssessmentBuilder().dimensions || [];
}

function getAssessmentDimensionByKey(key) {
  return assessmentDimensions().find((item) => item.key === key) || null;
}

function formatAssessmentDimensionName(key) {
  const dimension = getAssessmentDimensionByKey(key);
  return dimension ? dimension.name : '';
}

function formatAssessmentTypeName(dimensionKey, typeKey) {
  const dimension = getAssessmentDimensionByKey(dimensionKey);
  const type = dimension?.types?.find((item) => item.key === typeKey);
  return type ? type.name : '';
}

function assessmentTemplateGroups() {
  const groups = new Map();
  (state.questionnaire?.questions || []).forEach((question) => {
    const templateId = String(question.assessment_template_id || '').trim();
    if (!templateId) return;
    if (!groups.has(templateId)) {
      groups.set(templateId, {
        id: templateId,
        name: question.assessment_template_name || DEFAULT_ASSESSMENT_TEMPLATE_NAME,
        questions: [],
      });
    }
    groups.get(templateId).questions.push(question);
  });
  return [...groups.values()];
}

function assessmentTemplateGroupById(templateId) {
  return assessmentTemplateGroups().find((item) => item.id === templateId) || null;
}

function selectAssessmentTemplateGroup(templateId) {
  state.ruleMode = false;
  state.selection = { kind: 'assessment_template_group', templateId };
  renderWorkspace();
}

function removeAssessmentTemplateGroup(templateId) {
  state.questionnaire.questions = state.questionnaire.questions
    .filter((question) => question.assessment_template_id !== templateId)
    .map((question, index) => ({ ...question, sort_order: index + 1 }));
  const hasAssessmentQuestion = state.questionnaire.questions.some((question) => question.assessment_dimension_key);
  if (!hasAssessmentQuestion) {
    state.questionnaire.assessment_enabled = false;
  }
  state.selection = { kind: 'questionnaire' };
  renderWorkspace();
  showToast('已删除整组测评模板');
}

function ensureAssessmentTemplateIfEmpty() {
  const builder = ensureAssessmentBuilder();
  if (!builder.dimensions.length) {
    state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(buildDefaultAssessmentConfig());
  }
  return state.questionnaire.assessment_builder;
}

function buildUnknownTag(tagId) {
  return { tag_id: tagId, tag_name: '未匹配标签', group_name: '' };
}

function ensureTagKnown(tagId) {
  return state.availableTagMap.get(tagId) || buildUnknownTag(tagId);
}

function formatTagLabel(tag) {
  return `${tag.group_name ? `${tag.group_name} / ` : ''}${tag.tag_name || '未匹配标签'}`;
}

function formatTagGroupName(tag) {
  return tag.group_name || '未分组';
}

function buildTagBadges(tagIds) {
  const ids = normalizeTagIds(tagIds);
  if (!ids.length) return '<span class="tag-picker-note">未选择标签</span>';
  return ids.map((tagId) => {
    const tag = ensureTagKnown(tagId);
    const isUnknown = !state.availableTagMap.has(tagId);
    return `<span class="tag-badge${isUnknown ? ' unknown' : ''}">${escapeHtml(formatTagLabel(tag))}</span>`;
  }).join('');
}

function buildPublicUrl(questionnaire = state.questionnaire) {
  if (!questionnaire) return '';
  if (questionnaire.public_url) return questionnaire.public_url;
  const slug = String(questionnaire.slug || '').trim();
  return slug ? `${window.location.origin}/s/${slug}` : '';
}

function questionnaireDisplayName(item, fallback = '未命名问卷') {
  const title = String(item?.title || '').trim();
  const name = String(item?.name || '').trim();
  return title || name || fallback;
}

function assessmentTemplateReferenceId(item) {
  const rawId = String(item?.id ?? '').trim();
  if (rawId) return `questionnaire_template_${rawId}`;
  const config = normalizeAssessmentConfig(item?.assessment_config);
  return String(config.template_id || DEFAULT_ASSESSMENT_TEMPLATE_ID).trim() || DEFAULT_ASSESSMENT_TEMPLATE_ID;
}

function assessmentTemplateReferenceName(item) {
  return questionnaireDisplayName(item, DEFAULT_ASSESSMENT_TEMPLATE_NAME);
}

function assessmentTemplateDimensionCount(item) {
  const config = normalizeAssessmentConfig(item?.assessment_config);
  return Array.isArray(config.dimensions) ? config.dimensions.length : 0;
}

function assessmentTemplateQuestionCount(item) {
  if (Array.isArray(item?.questions)) {
    const assessmentQuestions = item.questions.filter((question) => question.assessment_dimension_key);
    return assessmentQuestions.length || item.questions.length;
  }
  return Number(item?.question_count || 0);
}

function assessmentAssetKind(item) {
  const config = normalizeAssessmentConfig(item?.assessment_config);
  return String(config.asset_kind || '').trim();
}

function isSavedAssessmentTemplateAsset(item) {
  if (!item || !item.assessment_enabled) return false;
  const config = normalizeAssessmentConfig(item.assessment_config);
  const assetKind = assessmentAssetKind(item);
  if (assetKind) return assetKind === 'assessment_template';
  return String(config.template_id || '').trim() === DEFAULT_ASSESSMENT_TEMPLATE_ID;
}

function isAssessmentTemplateCandidate(item) {
  if (!isSavedAssessmentTemplateAsset(item)) return false;
  if (state.currentId && Number(item.id) === Number(state.currentId)) return false;
  return true;
}

function assessmentTemplateBadge(item) {
  if (isSavedAssessmentTemplateAsset(item)) return '<span class="type-badge">测评模板</span>';
  return Boolean(
    item?.assessment_enabled
  ) ? '<span class="type-badge">多维测评</span>' : '';
}

function availableAssessmentTemplates() {
  return (state.list || []).filter((item) => isAssessmentTemplateCandidate(item));
}

function fieldLabel(fieldName) {
  const labels = {
    name: '问卷名称',
    title: '问卷标题',
    description: '问卷说明',
    slug: '分享标识',
    min_score: '最低分',
    max_score: '最高分',
    sort_order: '排序',
    required: '必填',
    option_text: '选项文案',
    score: '分值',
    tag_codes: '标签',
    assessment_config: '测评结果规则',
    assessment_dimension_key: '测评维度',
    assessment_type_key: '测评分型',
  };
  return labels[fieldName] || fieldName;
}

function questionLabel(rawTitle) {
  const title = String(rawTitle || '').trim();
  return title ? `题目“${title}”` : '该题目';
}

function humanizeErrorMessage(rawMessage, fallback = '操作失败，请稍后重试') {
  const message = String(rawMessage || '').trim();
  if (!message) return fallback;
  if (/[一-龥]/.test(message) && !/ is required|must be|unknown_|already_submitted|wechat_oauth_not_configured/i.test(message)) {
    return message;
  }

  if (message === 'name is required') return '请输入问卷名称';
  if (message === 'title is required') return '请输入问卷标题';
  if (message === 'questions must be an array') return '题目数据格式不正确，请重新添加题目';
  if (message === 'score must be an integer') return '分值必须填写数字';
  if (message === 'tag_codes must be an array') return '标签数据格式不正确，请重新选择标签';
  if (message === 'answers is required') return '请先填写问卷内容再提交';
  if (message === 'unknown question_id') return '检测到异常题目数据，请刷新页面后重试';
  if (message === 'already_submitted') return '你已经提交过这份问卷';
  if (message === 'wechat_oauth_not_configured') return '当前未完成微信授权配置，暂时无法使用该功能';
  if (message === 'question type must be single_choice, multi_choice, textarea or mobile') return '题型不正确，请重新选择题型';
  if (message === 'min_score must be an integer') return '最低分必须填写数字';
  if (message === 'max_score must be an integer') return '最高分必须填写数字';
  if (message === 'option_text is required') return '请输入选项文案';
  if (message === 'score rule min_score cannot be greater than max_score') return '请检查分数规则：最低分不能大于最高分';
  if (message === 'score rule tag_codes must be an array') return '分数规则标签格式不正确，请重新选择标签';
  if (message === 'slug already exists') return '分享标识已存在，请修改右侧分享标识后再保存';
  if (message === 'assessment_config must be an object') return '测评结果规则必须是一个 JSON 对象';

  let matched = message.match(/^([a-z_]+) is required$/i);
  if (matched) return `请输入${fieldLabel(matched[1])}`;

  matched = message.match(/^([a-z_]+) must be an integer$/i);
  if (matched) return `${fieldLabel(matched[1])}必须填写数字`;

  matched = message.match(/^question ['"]?(.*?)['"]? is required$/i);
  if (matched) return `${questionLabel(matched[1])}还未填写，请补充后再保存`;

  matched = message.match(/^question ['"]?(.*?)['"]? must have options$/i);
  if (matched) return `${questionLabel(matched[1])}至少需要一个选项，请补充后再保存`;

  matched = message.match(/^question ['"]?(.*?)['"]? has an invalid option$/i);
  if (matched) return `${questionLabel(matched[1])}存在无效选项，请检查选项内容`;

  matched = message.match(/^question ['"]?(.*?)['"]? only allows one option$/i);
  if (matched) return `${questionLabel(matched[1])}只能选择一个选项，请检查当前配置`;

  matched = message.match(/^unknown question_id:?/i);
  if (matched) return '检测到异常题目数据，请刷新页面后重试';

  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) return '网络连接异常，请稍后重试';
  if (/Unexpected token|JSON/i.test(message)) return fallback;

  return fallback;
}

function extractErrorMessage(data) {
  if (!data) return '';
  if (typeof data === 'string') return data;
  const detail = data.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return detail.message || detail.error || detail.detail || detail.page_error || '';
  }
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] || {};
    return first.msg || first.message || first.error || '';
  }
  return data.message || data.error || data.page_error || '';
}

function showToast(message, isError = false) {
  if (!message) return;
  toastEl.textContent = message;
  toastEl.className = `toast${isError ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastEl.className = 'toast hidden';
  }, isError ? 18000 : 12600);
}

function openDrawer(title, rows = []) {
  drawerTitleEl.textContent = title;
  drawerBodyEl.innerHTML = rows.map((row) => `
    <div class="drawer-row">
      <span>${escapeHtml(row.label || '')}</span>
      <strong>${escapeHtml(row.value || '-')}</strong>
    </div>
  `).join('');
  drawerOverlayEl.classList.remove('hidden');
}

function closeDrawer() {
  drawerOverlayEl.classList.add('hidden');
}

function applyTagSelection(target, tagIds) {
  const merged = normalizeTagIds(tagIds);
  if (target?.type === 'option') {
    const question = state.questionnaire.questions.find((item) => item.local_key === target.questionKey);
    const option = question?.options.find((item) => item.local_key === target.optionKey);
    if (option) option.tag_codes = merged;
  }
  if (target?.type === 'rule') {
    const rule = state.questionnaire.score_rules.find((item) => item.local_key === target.ruleKey);
    if (rule) rule.tag_codes = merged;
  }
  if (target?.type === 'assessment_type') {
    const dimension = assessmentDimensions().find((item) => item.key === target.dimensionKey);
    const type = (dimension?.types || []).find((item) => item.key === target.typeKey);
    if (type) type.tag_codes = merged;
  }
  if (target?.type === 'assessment_overall_level') {
    const level = (ensureAssessmentBuilder().overall_levels || []).find((item) => item.local_key === target.levelKey);
    if (level) level.tag_codes = merged;
  }
  renderWorkspace();
}

function openTagModal(target, selectedTagIds = []) {
  if (!window.AICRMWeComTagPicker) {
    showToast('标签选择器加载失败，请刷新后重试', true);
    return;
  }
  const selected = normalizeTagIds(selectedTagIds).map((tagId) => ensureTagKnown(tagId));
  window.AICRMWeComTagPicker.open({
    title: '选择标签',
    mode: 'multiple',
    value: selected,
    catalog: { items: state.availableTags },
    onConfirm: (tags) => {
      applyTagSelection(target, normalizeTagIds((tags || []).map((tag) => tag.tag_id)));
    },
    onClear: () => {
      applyTagSelection(target, []);
    },
  });
}

function normalizeOtherMaxLength(value, fallback = 80) {
  if (value === '' || value === null || value === undefined) return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function createOption(option = {}, index = 0) {
  return {
    id: option.id ?? null,
    local_key: option.local_key || nextLocalKey('option'),
    option_text: option.option_text || '',
    score: option.score ?? 0,
    assessment_type_key: option.assessment_type_key || '',
    tag_codes: normalizeTagIds(option.tag_codes || []),
    is_other: Boolean(option.is_other),
    other_placeholder: option.other_placeholder || '',
    other_max_length: normalizeOtherMaxLength(option.other_max_length),
    sort_order: option.sort_order ?? (index + 1),
  };
}

function createQuestion(type = 'single_choice', question = {}, index = 0) {
  const normalizedType = question.type || type;
  return {
    id: question.id ?? null,
    local_key: question.local_key || nextLocalKey('question'),
    type: normalizedType,
    title: question.title || '',
    placeholder_text: question.placeholder_text || '',
    assessment_dimension_key: question.assessment_dimension_key || '',
    sidebar_profile_field: normalizeSidebarProfileField(question.sidebar_profile_field),
    assessment_template_id: question.assessment_template_id || '',
    assessment_template_name: question.assessment_template_name || '',
    required: Boolean(question.required),
    sort_order: question.sort_order ?? (index + 1),
    options: ['textarea', 'mobile'].includes(normalizedType)
      ? []
      : (question.options || []).map((option, optionIndex) => createOption(option, optionIndex)).length
        ? (question.options || []).map((option, optionIndex) => createOption(option, optionIndex))
        : [createOption({}, 0)],
  };
}

function createQuestionFromAssessmentPreset(item, index = 0) {
  const presetQuestion = Array.isArray(item)
    ? { dim: item[0], title: item[1], options: item[2] }
    : (item || {});
  const question = {
    type: 'single_choice',
    title: presetQuestion.title,
    required: true,
    assessment_dimension_key: presetQuestion.dim,
    assessment_template_id: DEFAULT_ASSESSMENT_TEMPLATE_ID,
    assessment_template_name: DEFAULT_ASSESSMENT_TEMPLATE_NAME,
    sort_order: index + 1,
    options: (presetQuestion.options || []).map((option, optionIndex) => ({
      option_text: option[2],
      score: option[1],
      assessment_type_key: option[0],
      tag_codes: [],
      sort_order: optionIndex + 1,
    })),
  };
  return createQuestion('single_choice', question, index);
}

function createQuestionFromAssessmentTemplateQuestion(item, index = 0, templateReference = {}) {
  const questionType = item.type || 'single_choice';
  return createQuestion(questionType, {
    id: null,
    type: questionType,
    title: item.title || '',
    placeholder_text: item.placeholder_text || '',
    required: Boolean(item.required),
    assessment_dimension_key: item.assessment_dimension_key || '',
    assessment_template_id: templateReference.id || '',
    assessment_template_name: templateReference.name || DEFAULT_ASSESSMENT_TEMPLATE_NAME,
    sort_order: index + 1,
    options: (item.options || []).map((option, optionIndex) => ({
      id: null,
      option_text: option.option_text || '',
      score: option.score ?? 0,
      assessment_type_key: option.assessment_type_key || '',
      tag_codes: normalizeTagIds(option.tag_codes || []),
      sort_order: optionIndex + 1,
    })),
  }, index);
}

function createRule(rule = {}, index = 0) {
  return {
    id: rule.id ?? null,
    local_key: rule.local_key || nextLocalKey('rule'),
    min_score: rule.min_score ?? '',
    max_score: rule.max_score ?? '',
    tag_codes: normalizeTagIds(rule.tag_codes || []),
    sort_order: rule.sort_order ?? (index + 1),
  };
}

function normalizeAnswerDisplayMode(value) {
  return ['all_in_one', 'one_by_one'].includes(value) ? value : 'all_in_one';
}

function blankQuestionnaire() {
  const defaultPreset = editorConfig.defaultAssessment ? buildSiyuanIpAssessmentPreset() : null;
  const defaultAssessmentConfig = defaultPreset ? defaultPreset.assessment_config : {};
  const defaultAssessmentBuilder = createAssessmentBuilderFromConfig(defaultAssessmentConfig);
  return {
    id: null,
    public_url: '',
    name: defaultPreset ? defaultPreset.name : '',
    title: defaultPreset ? defaultPreset.title : '',
    description: defaultPreset ? defaultPreset.description : '',
    answer_display_mode: 'all_in_one',
    assessment_enabled: Boolean(editorConfig.defaultAssessment),
    assessment_config: defaultAssessmentConfig,
    assessment_builder: defaultAssessmentBuilder,
    slug: '',
    is_disabled: false,
    questions: defaultPreset
      ? defaultPreset.questions.map((question, index) => createQuestionFromAssessmentPreset(question, index))
      : [],
    score_rules: [],
  };
}

function hydrateQuestionnaire(source = null) {
  const draft = blankQuestionnaire();
  const questionnaire = source || {};
  const hasSource = Boolean(source);
  const assessmentConfig = hasSource
    ? normalizeAssessmentConfig(questionnaire.assessment_config)
    : draft.assessment_config;
  const assessmentBuilder = hasSource
    ? createAssessmentBuilderFromConfig(assessmentConfig)
    : draft.assessment_builder;
  const templateId = String(assessmentConfig.template_id || '').trim();
  const templateName = String(assessmentConfig.template_name || '').trim() || DEFAULT_ASSESSMENT_TEMPLATE_NAME;
  const sourceQuestions = hasSource ? (questionnaire.questions || []) : draft.questions;
  const hydratedQuestions = sourceQuestions.map((question, index) => createQuestion(question.type || 'single_choice', question, index))
    .map((question) => (
      templateId && question.assessment_dimension_key
        ? {
            ...question,
            assessment_template_id: templateId,
            assessment_template_name: templateName,
          }
        : question
    ));
  return {
    ...draft,
    id: questionnaire.id ?? null,
    public_url: questionnaire.public_url || '',
    name: questionnaire.name || '',
    title: questionnaire.title || '',
    description: questionnaire.description || '',
    answer_display_mode: normalizeAnswerDisplayMode(questionnaire.answer_display_mode),
    assessment_enabled: hasSource ? Boolean(questionnaire.assessment_enabled) : draft.assessment_enabled,
    assessment_config: assessmentConfig,
    assessment_builder: assessmentBuilder,
    slug: questionnaire.slug || '',
    is_disabled: Boolean(questionnaire.is_disabled),
    questions: hydratedQuestions,
    score_rules: (questionnaire.score_rules || []).map((rule, index) => createRule(rule, index)),
  };
}

function currentQuestion() {
  if (state.selection.kind !== 'question') return null;
  return state.questionnaire.questions.find((item) => item.local_key === state.selection.key) || null;
}

function currentRule() {
  if (state.selection.kind !== 'rule') return null;
  return state.questionnaire.score_rules.find((item) => item.local_key === state.selection.key) || null;
}

function selectQuestionnaire() {
  state.ruleMode = false;
  state.selection = { kind: 'questionnaire' };
  renderWorkspace();
}

function selectAssessmentSettings() {
  state.ruleMode = false;
  state.questionnaire.assessment_enabled = true;
  ensureAssessmentTemplateIfEmpty();
  state.selection = { kind: 'assessment' };
  renderWorkspace();
}

function selectQuestion(key) {
  state.ruleMode = false;
  state.selection = { kind: 'question', key };
  renderWorkspace();
}

function selectRule(key) {
  state.ruleMode = true;
  state.lastRuleKey = key;
  state.selection = { kind: 'rule', key };
  renderWorkspace();
}

function enterRuleMode() {
  state.ruleMode = true;
  if (state.questionnaire.score_rules.length) {
    const remembered = state.questionnaire.score_rules.find((item) => item.local_key === state.lastRuleKey);
    const target = remembered || state.questionnaire.score_rules[0];
    state.lastRuleKey = target.local_key;
    state.selection = { kind: 'rule', key: target.local_key };
  } else {
    state.lastRuleKey = '';
    state.selection = { kind: 'questionnaire' };
  }
  renderWorkspace();
}

function resetDraft(data = null) {
  state.questionnaire = hydrateQuestionnaire(data);
  state.currentId = state.questionnaire.id;
  state.ruleMode = false;
  state.lastRuleKey = state.questionnaire.score_rules[0]?.local_key || '';
  state.selection = editorConfig.defaultAssessment && !data
    ? { kind: 'question', key: state.questionnaire.questions[0]?.local_key || '' }
    : { kind: 'questionnaire' };
  rememberDraftSnapshot();
  renderWorkspace();
}

function sourceQuestionnaireIdFromTemplateGroup(group) {
  const matched = String(group?.id || '').match(/^questionnaire_template_(\d+)$/);
  return matched ? Number(matched[1]) : '';
}

function buildSerializableAssessmentConfig() {
  const config = buildAssessmentConfigFromBuilder(ensureAssessmentBuilder());
  if (editorConfig.defaultAssessment) {
    config.asset_kind = 'assessment_template';
    delete config.source_questionnaire_id;
    return config;
  }
  if (config.asset_kind === 'assessment_template') {
    delete config.asset_kind;
  }
  const group = assessmentTemplateGroups()[0] || null;
  if (group) {
    config.asset_kind = 'assessment_template_reference';
    if (!config.source_questionnaire_id) {
      const sourceId = sourceQuestionnaireIdFromTemplateGroup(group);
      if (sourceId) config.source_questionnaire_id = sourceId;
    }
  }
  return config;
}

function serializePayload(options = {}) {
  return {
    name: state.questionnaire.name,
    title: state.questionnaire.title,
    description: state.questionnaire.description,
    answer_display_mode: normalizeAnswerDisplayMode(state.questionnaire.answer_display_mode),
    assessment_enabled: Boolean(state.questionnaire.assessment_enabled),
    assessment_config: state.questionnaire.assessment_enabled
      ? buildSerializableAssessmentConfig()
      : buildAssessmentConfigFromBuilder(ensureAssessmentBuilder()),
    slug: state.questionnaire.slug,
    is_disabled: state.questionnaire.is_disabled,
    questions: state.questionnaire.questions.map((question, index) => {
      const payload = {
        type: question.type,
        title: question.title,
        assessment_dimension_key: question.assessment_dimension_key || '',
        sidebar_profile_field: normalizeSidebarProfileField(question.sidebar_profile_field),
        required: Boolean(question.required),
        sort_order: Number(question.sort_order || (index + 1)),
      };
      if (['textarea', 'mobile'].includes(question.type)) {
        payload.placeholder_text = question.placeholder_text || '';
      }
      if (!['textarea', 'mobile'].includes(question.type)) {
        payload.options = question.options.map((option, optionIndex) => ({
          option_text: option.option_text,
          score: Number(option.score || 0),
          assessment_type_key: option.assessment_type_key || '',
          tag_codes: normalizeTagIds(option.tag_codes),
          is_other: Boolean(option.is_other),
          other_placeholder: option.other_placeholder || '',
          other_max_length: normalizeOtherMaxLength(option.other_max_length),
          sort_order: Number(option.sort_order || (optionIndex + 1)),
        }));
      }
      return payload;
    }),
    score_rules: state.questionnaire.score_rules.map((rule, index) => ({
      min_score: rule.min_score,
      max_score: rule.max_score,
      tag_codes: normalizeTagIds(rule.tag_codes),
      sort_order: Number(rule.sort_order || (index + 1)),
    })),
  };
}

function serializeDraftSnapshot() {
  return JSON.stringify(serializePayload({ validateCompletion: false }));
}

function isDraftDirty() {
  return Boolean(state.questionnaire) && state.initialSnapshot !== serializeDraftSnapshot();
}

function updateDraftIndicator() {
  draftIndicatorEl.classList.toggle('hidden', !isDraftDirty());
}

function rememberDraftSnapshot() {
  state.initialSnapshot = state.questionnaire ? serializeDraftSnapshot() : '';
  state.persistedIsDisabled = Boolean(state.currentId && state.questionnaire && state.questionnaire.is_disabled);
  updateDraftIndicator();
}

function confirmDiscardChanges() {
  if (!isDraftDirty()) return true;
  return window.confirm('当前有未保存修改，确认放弃并继续吗？');
}

function normalizeDateValue(value) {
  const raw = String(value || '').trim();
  if (!raw) return 0;
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  const timestamp = new Date(normalized).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function formatDateTime(value) {
  const raw = String(value || '').trim();
  if (!raw) return '-';
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return raw;
  const pad = (number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function filteredQuestionnaires() {
  const keyword = state.listSearch.trim().toLowerCase();
  return [...state.list]
    .filter((item) => {
      if (state.statusFilter === 'enabled' && item.is_disabled) return false;
      if (state.statusFilter === 'disabled' && !item.is_disabled) return false;
      if (!keyword) return true;
      return String(item.name || '').toLowerCase().includes(keyword);
    })
    .sort((left, right) => {
      const statusDiff = Number(Boolean(left.is_disabled)) - Number(Boolean(right.is_disabled));
      if (statusDiff !== 0) return statusDiff;
      return normalizeDateValue(right.created_at) - normalizeDateValue(left.created_at);
    });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(humanizeErrorMessage(extractErrorMessage(data), '请求失败，请稍后重试'));
  }
  return data;
}

async function loadAvailableTags() {
  try {
    const data = await fetchJson('/api/admin/wecom/tags');
    state.availableTags = data.items || [];
    state.availableTagMap = new Map(state.availableTags.map((item) => [item.tag_id, item]));
    if (data.degraded || !state.availableTags.length) {
      tagCatalogMessageEl.textContent = data.page_error || '当前未获取到企微标签，可稍后重试';
      tagCatalogMessageEl.className = 'inline-alert warning';
    } else {
      tagCatalogMessageEl.textContent = '';
      tagCatalogMessageEl.className = 'inline-alert hidden';
    }
  } catch (error) {
    state.availableTags = [];
    state.availableTagMap = new Map();
    tagCatalogMessageEl.textContent = '企微标签加载失败，可稍后重试';
    tagCatalogMessageEl.className = 'inline-alert error';
  }
  renderInspector();
}

async function loadList() {
  state.loadingList = true;
  if (listEl) {
    listEl.innerHTML = '<div class="empty-state">问卷列表加载中...</div>';
  }
  try {
    const data = await fetchJson('/api/admin/questionnaires');
    state.list = data.questionnaires || [];
    if (listEl) {
      renderList();
    }
  } catch (error) {
    state.list = [];
    if (listEl) {
      listEl.innerHTML = `<div class="empty-state">${escapeHtml(error.message || '问卷列表加载失败，请稍后重试')}</div>`;
    }
  } finally {
    state.loadingList = false;
  }
}

async function loadQuestionnaire(questionnaireId, options = {}) {
  if (!options.skipConfirm && !confirmDiscardChanges()) return;
  const data = await fetchJson(`/api/admin/questionnaires/${questionnaireId}`);
  resetDraft(data.questionnaire);
  renderList();
}

function startNewQuestionnaire(options = {}) {
  if (!options.skipConfirm && !confirmDiscardChanges()) return;
  resetDraft();
}

async function toggleQuestionnaire(item) {
  await fetchJson(`/api/admin/questionnaires/${item.id}/disable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_disabled: !item.is_disabled }),
  });
  showToast(item.is_disabled ? '问卷已启用' : '问卷已停用');
  await loadList();
  if (state.currentId === item.id) {
    await loadQuestionnaire(item.id, { skipConfirm: true });
  }
}

async function deleteQuestionnaireItem(item) {
  if (!window.confirm('删除后不可恢复，确认删除该问卷吗？')) return;
  await fetchJson(`/api/admin/questionnaires/${item.id}`, { method: 'DELETE' });
  if (state.currentId === item.id) {
    window.location.assign(editorConfig.backHref);
    return;
  }
  showToast('问卷已删除');
  await loadList();
}

async function duplicateQuestionnaire(item) {
  if (!item?.id) return;
  if (!confirmDiscardChanges()) return;
  if (!window.confirm(`复制问卷“${item.name || item.title || '未命名问卷'}”？复制后会生成一份默认停用的新问卷。`)) return;
  const data = await fetchJson(`/api/admin/questionnaires/${item.id}/duplicate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  const copied = data.questionnaire || {};
  await loadList();
  if (copied.id) {
    await loadQuestionnaire(copied.id, { skipConfirm: true });
  }
  showToast('问卷已复制，默认停用');
}

async function copyText(text, successMessage = '链接已复制') {
  if (!text) {
    showToast('当前还没有可复制的链接', true);
    return;
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      window.prompt('请复制以下链接', text);
    }
    showToast(successMessage);
  } catch (error) {
    window.prompt('请手动复制以下链接', text);
  }
}

function downloadFilenameFromResponse(response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) return decodeURIComponent(utf8Match[1]);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  return asciiMatch ? asciiMatch[1] : fallback;
}

async function downloadQuestionnaireData(questionnaireId) {
  if (!questionnaireId) {
    showToast('保存问卷后才能下载数据', true);
    return;
  }
  const response = await fetch(`/api/admin/questionnaires/${questionnaireId}/export`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(humanizeErrorMessage(extractErrorMessage(data), '下载失败，请稍后重试'));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = downloadFilenameFromResponse(response, `questionnaire-${questionnaireId}-submissions.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast('下载已开始');
}

function renderManagementTable() {
  if (!listEl) return;
  if (!state.list.length) {
    listEl.innerHTML = '<div class="empty-state">还没有问卷，点击「创建新问卷」开始搭建。</div>';
    return;
  }
  const items = filteredQuestionnaires();
  if (!items.length) {
    listEl.innerHTML = '<div class="empty-state">没有符合当前筛选条件的问卷。</div>';
    return;
  }
  listEl.innerHTML = `
    <table class="management-table">
      <thead>
        <tr>
          <th>问卷名称</th>
          <th>问卷创建时间</th>
          <th>提交数</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((item) => `
          <tr class="management-row${state.currentId === item.id ? ' active' : ''}${item.is_disabled ? ' disabled' : ''}" data-id="${escapeHtml(String(item.id))}">
            <td>
              <div class="name-line">
                <span class="name-main">${escapeHtml(item.name || '未命名问卷')}</span>
                ${assessmentTemplateBadge(item)}
                <span class="status-badge${item.is_disabled ? ' disabled' : ''}">${item.is_disabled ? '已停用' : '启用中'}</span>
              </div>
              ${item.title ? `<div class="name-sub">${escapeHtml(item.title)}</div>` : ''}
            </td>
            <td><span class="table-text">${escapeHtml(formatDateTime(item.created_at))}</span></td>
            <td><span class="table-number">${escapeHtml(String(item.submission_count || 0))}</span></td>
            <td>
              <div class="row-actions">
                <button type="button" class="mini-btn edit" data-action="edit">编辑</button>
                <button type="button" class="mini-btn duplicate" data-action="duplicate">复制</button>
                <button type="button" class="mini-btn toggle" data-action="toggle">${item.is_disabled ? '启用' : '停用'}</button>
                <button type="button" class="mini-btn share" data-action="share">分享</button>
                <button type="button" class="mini-btn export" data-action="export">下载数据</button>
              </div>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  listEl.querySelectorAll('.management-row').forEach((row) => {
    const item = state.list.find((entry) => String(entry.id) === row.dataset.id);
    if (!item) return;
    row.querySelector('[data-action="edit"]').addEventListener('click', () => {
      loadQuestionnaire(item.id).catch((error) => showToast(error.message || '问卷加载失败，请稍后重试', true));
    });
    row.querySelector('[data-action="toggle"]').addEventListener('click', () => {
      toggleQuestionnaire(item).catch((error) => showToast(error.message || '问卷状态更新失败，请稍后重试', true));
    });
    row.querySelector('[data-action="duplicate"]').addEventListener('click', () => {
      duplicateQuestionnaire(item).catch((error) => showToast(error.message || '问卷复制失败，请稍后重试', true));
    });
    row.querySelector('[data-action="share"]').addEventListener('click', () => {
      copyText(item.public_url, '分享链接已复制');
    });
    row.querySelector('[data-action="export"]').addEventListener('click', () => {
      downloadQuestionnaireData(item.id).catch((error) => showToast(error.message || '下载失败，请稍后重试', true));
    });
  });
}

function renderList() {
  if (!listEl) return;
  renderManagementTable();
}

function renderEditorSecondaryActions() {
  if (!editorSecondaryActionsEl) return;
  if (editorConfig.defaultAssessment) {
    editorSecondaryActionsEl.innerHTML = '<button id="editor-assessment-result-preview-btn" type="button" class="btn ghost">打开 H5 结果页</button>';
    document.getElementById('editor-assessment-result-preview-btn')?.addEventListener('click', () => {
      const publicUrl = buildPublicUrl();
      if (!publicUrl) {
        showToast('保存模板后可打开 H5 结果页', true);
        return;
      }
      window.open(publicUrl, '_blank');
    });
    return;
  }
  editorSecondaryActionsEl.innerHTML = state.currentId ? `
      <button id="editor-share-btn" type="button" class="btn ghost">分享</button>
      <button id="editor-duplicate-btn" type="button" class="btn ghost">复制问卷</button>
      <button id="editor-export-btn" type="button" class="btn ghost">下载数据</button>
    ` : '';
  const shareBtn = document.getElementById('editor-share-btn');
  const duplicateBtn = document.getElementById('editor-duplicate-btn');
  const exportBtn = document.getElementById('editor-export-btn');
  shareBtn?.addEventListener('click', () => {
    copyText(buildPublicUrl(), '分享链接已复制');
  });
  duplicateBtn?.addEventListener('click', () => {
    duplicateQuestionnaire({ ...(state.questionnaire || {}), id: state.currentId }).catch((error) => showToast(error.message || '问卷复制失败，请稍后重试', true));
  });
  exportBtn?.addEventListener('click', () => {
    downloadQuestionnaireData(state.currentId).catch((error) => showToast(error.message || '下载失败，请稍后重试', true));
  });
}

function renderTopbar() {
  const title = questionnaireDisplayName(state.questionnaire, state.currentId ? '编辑问卷' : '新建问卷');
  const pageKind = state.currentId ? '编辑问卷' : (editorConfig.defaultAssessment ? '创建测评问卷模板' : '新建问卷');
  topbarTitleEl.textContent = editorConfig.defaultAssessment ? pageKind : title;
  const topbarSubtitleEl = document.getElementById('topbar-subtitle');
  if (topbarSubtitleEl) {
    topbarSubtitleEl.textContent = editorConfig.defaultAssessment
      ? '基础信息、设置维度、结果配置、H5 预览 / 发布按步骤配置。'
      : '';
  }
  if (editorPageTitleEl) {
    editorPageTitleEl.textContent = pageKind;
  }
  document.title = `${pageKind} · ${title || '问卷编辑'}`;
  renderEditorSecondaryActions();
  updateDraftIndicator();
}

function renderAssessmentSummary() {
  updateDraftIndicator();
}

function renderPreview() {
  const questionnaire = state.questionnaire;
  const isQuestionnaireSelected = state.selection.kind === 'questionnaire';
  previewHeadEl.className = `preview-head${isQuestionnaireSelected ? ' active' : ''}`;
  if (editorConfig.defaultAssessment) {
    previewHeadEl.innerHTML = `
      <div class="question-type">测评模板预览</div>
      <h2>${escapeHtml(questionnaire.title || '小 IP 商业力测评')}</h2>
      <p>模板含 <b>${escapeHtml(String(assessmentDimensions().length))}</b> 个维度、<b>${escapeHtml(String(questionnaire.questions.length))}</b> 道题。题目可继续增删，维度不固定。</p>
    `;
  } else {
    previewHeadEl.innerHTML = `
      <div class="question-type">问卷设置</div>
      <h2>${escapeHtml(questionnaire.title || '问卷标题')}</h2>
      <p>${escapeHtml(questionnaire.description || '在右侧编辑问卷信息')}</p>
    `;
  }
  previewHeadEl.onclick = () => selectQuestionnaire();

  previewQuestionsEl.innerHTML = '';
  if (!questionnaire.questions.length) {
    previewQuestionsEl.innerHTML = '<div class="empty-state">从左侧添加题目</div>';
  } else {
    const renderedTemplateIds = new Set();
    questionnaire.questions.forEach((question, questionIndex) => {
      if (question.assessment_template_id && !editorConfig.defaultAssessment) {
        if (renderedTemplateIds.has(question.assessment_template_id)) return;
        renderedTemplateIds.add(question.assessment_template_id);
        const group = assessmentTemplateGroupById(question.assessment_template_id);
        if (!group) return;
        const groupDimensions = [...new Set(group.questions.map((item) => item.assessment_dimension_key).filter(Boolean))];
        const groupCard = document.createElement('article');
        groupCard.className = `preview-template-group${state.selection.kind === 'assessment_template_group' && state.selection.templateId === group.id ? ' active' : ''}`;
        groupCard.innerHTML = `
          <span class="question-type">多维测评模板 · 整组引用</span>
          <h4>${escapeHtml(group.name)}</h4>
          <div class="muted">这组题来自同一个测评模板，普通问卷里按整组添加、整组删除。</div>
          <div class="template-group-meta">
            <span class="template-group-chip">${escapeHtml(String(groupDimensions.length))} 个维度</span>
            <span class="template-group-chip">${escapeHtml(String(group.questions.length))} 道题</span>
            <span class="template-group-chip">含结果页规则</span>
          </div>
          <div class="preview-options">
            ${group.questions.slice(0, 3).map((item) => `
              <div class="option-pill">
                ${escapeHtml(item.title || '题目标题')}
                <small>${escapeHtml(formatAssessmentDimensionName(item.assessment_dimension_key) || item.assessment_dimension_key || '未选择维度')}</small>
              </div>
            `).join('')}
            ${group.questions.length > 3 ? `<div class="option-pill">其余 ${escapeHtml(String(group.questions.length - 3))} 题随模板一起保存</div>` : ''}
          </div>
        `;
        groupCard.addEventListener('click', () => selectAssessmentTemplateGroup(group.id));
        previewQuestionsEl.appendChild(groupCard);
        return;
      }
      const card = document.createElement('article');
      card.className = `preview-question${state.selection.kind === 'question' && state.selection.key === question.local_key ? ' active' : ''}`;
      if (editorConfig.defaultAssessment) {
        const dimensionName = formatAssessmentDimensionName(question.assessment_dimension_key) || question.assessment_dimension_key || '未选择维度';
        const optionPreview = (question.options || []).map((option) => `
          <div class="option-pill">
            <span>${escapeHtml(option.option_text || '选项')}</span>
            <small>${escapeHtml(formatAssessmentTypeName(question.assessment_dimension_key, option.assessment_type_key) || option.assessment_type_key || '未选择类型')} / ${escapeHtml(String(option.score ?? 0))}分</small>
          </div>
        `).join('');
        card.innerHTML = `
          <div class="question-meta-row"><span>Q${escapeHtml(String(questionIndex + 1).padStart(2, '0'))} / ${escapeHtml(dimensionName)}</span><span>单选测评题${sidebarProfileChipHtml(question)}</span></div>
          <h4>${escapeHtml(question.title || '题目标题')}</h4>
          <div class="preview-options">${optionPreview}</div>
        `;
      } else {
        const optionPreview = question.type === 'textarea'
          ? `<div class="option-pill">${escapeHtml(question.placeholder_text || '多行文本输入')}</div>`
          : question.type === 'mobile'
            ? `<div class="option-pill">${escapeHtml(question.placeholder_text || '请输入手机号')}</div>`
            : (question.options || []).map((option) => `
                <div class="option-pill">
                  ${escapeHtml(option.option_text || '选项')}
                  ${state.questionnaire.assessment_enabled && option.assessment_type_key
                    ? `<small>分型：${escapeHtml(formatAssessmentTypeName(question.assessment_dimension_key, option.assessment_type_key) || option.assessment_type_key)}</small>`
                    : ''}
                </div>
              `).join('');
        card.innerHTML = `
          <span class="question-type">
            ${escapeHtml(formatQuestionType(question.type))}${question.required ? ' · 必填' : ''}${state.questionnaire.assessment_enabled && question.assessment_dimension_key ? ` · ${escapeHtml(formatAssessmentDimensionName(question.assessment_dimension_key) || question.assessment_dimension_key)}` : ''}
          </span>
          ${sidebarProfileChipHtml(question)}
          <h4>${escapeHtml(question.title || '题目标题')}</h4>
          <div class="preview-options">${optionPreview}</div>
        `;
      }
      card.addEventListener('click', () => selectQuestion(question.local_key));
      previewQuestionsEl.appendChild(card);
    });
  }

  previewRulesWrapEl.classList.add('hidden');
  previewRulesWrapEl.innerHTML = '';
  updateDraftIndicator();
}

function mountTagPicker(host, selectedTagIds, onChange, target) {
  const normalizedSelected = normalizeTagIds(selectedTagIds);
  host.innerHTML = `
    <div class="tag-inline">
      <button type="button" class="btn ghost open-tag-modal">选择标签</button>
      <div class="tag-badges">${buildTagBadges(normalizedSelected)}</div>
    </div>
  `;
  const openButton = host.querySelector('.open-tag-modal');
  openButton.addEventListener('click', () => openTagModal(target, onChange.currentValue ? onChange.currentValue() : normalizedSelected));
}

function renderQuestionnaireInspector() {
  inspectorTitleEl.textContent = '问卷设置';
  inspectorSubtitleEl.textContent = '编辑当前问卷的基础信息。';
  const deleteDisabled = !state.persistedIsDisabled;
  const assessmentGroup = assessmentTemplateGroups()[0] || null;
  const deleteSection = state.currentId ? `
    <section class="config-group danger-zone">
      <button
        id="delete-questionnaire-btn"
        type="button"
        class="link-btn danger"
        ${deleteDisabled ? 'disabled title="请先停用后删除"' : ''}
      >
        删除此问卷
      </button>
      ${deleteDisabled ? '<p>请先停用问卷后再删除。</p>' : ''}
    </section>
  ` : '';
  inspectorBodyEl.innerHTML = `
    <section class="config-group">
      <label class="field">问卷名称
        <input id="field-name" type="text" value="${escapeHtml(state.questionnaire.name)}">
      </label>
      <label class="field">问卷标题
        <input id="field-title" type="text" value="${escapeHtml(state.questionnaire.title)}">
      </label>
      <label class="field">问卷说明
        <textarea id="field-description">${escapeHtml(state.questionnaire.description)}</textarea>
      </label>
      <label class="field">答题展示方式
        <select id="field-answer-display-mode">
          <option value="all_in_one" ${normalizeAnswerDisplayMode(state.questionnaire.answer_display_mode) === 'all_in_one' ? 'selected' : ''}>整页答题</option>
          <option value="one_by_one" ${normalizeAnswerDisplayMode(state.questionnaire.answer_display_mode) === 'one_by_one' ? 'selected' : ''}>一题一页</option>
        </select>
      </label>
      <div class="field-grid compact">
        <label class="field">分享标识
          <input id="field-slug" type="text" value="${escapeHtml(state.questionnaire.slug)}">
        </label>
        <label class="field"><span style="display:block;margin-bottom:7px;">问卷状态</span>
          <label class="field" style="margin-bottom:0;font-weight:600;">
            <input id="field-is-disabled" type="checkbox" ${state.questionnaire.is_disabled ? 'checked' : ''}> 停用问卷
          </label>
        </label>
      </div>
    </section>
    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>多维测评模板</h3>
          <p>普通问卷只引用模板，不在这里编辑模板内部题目、维度和结果页。</p>
        </div>
        <button id="open-assessment-config-btn" type="button" class="btn secondary">${assessmentGroup ? '查看已添加模板' : '添加多维测评模板'}</button>
      </div>
      <label class="field" style="margin-bottom:8px;font-weight:600;">
        <input id="field-assessment-enabled" type="checkbox" ${state.questionnaire.assessment_enabled ? 'checked' : ''}> 启用多维测评结果页
      </label>
      <div class="helper-note">
        ${assessmentGroup
          ? `已引用“${escapeHtml(assessmentGroup.name)}”，包含 ${escapeHtml(String(assessmentGroup.questions.length))} 道题。模板内部内容请到“创建测评问卷模板”里维护。`
          : '从已创建的测评模板中选择，插入后作为一整组题管理。提交后动作与外部推送请在列表的“运营配置”中维护。'}
      </div>
    </section>
    ${deleteSection}
  `;
  inspectorBodyEl.querySelector('#field-name').addEventListener('input', (event) => {
    state.questionnaire.name = event.target.value;
    renderTopbar();
  });
  inspectorBodyEl.querySelector('#field-title').addEventListener('input', (event) => {
    state.questionnaire.title = event.target.value;
    renderTopbar();
    renderPreview();
  });
  inspectorBodyEl.querySelector('#field-description').addEventListener('input', (event) => {
    state.questionnaire.description = event.target.value;
    renderPreview();
  });
  inspectorBodyEl.querySelector('#field-answer-display-mode').addEventListener('change', (event) => {
    state.questionnaire.answer_display_mode = normalizeAnswerDisplayMode(event.target.value);
    updateDraftIndicator();
  });
  inspectorBodyEl.querySelector('#field-assessment-enabled').addEventListener('change', (event) => {
    state.questionnaire.assessment_enabled = event.target.checked;
    if (event.target.checked) {
      ensureAssessmentTemplateIfEmpty();
    }
    renderPreview();
    updateDraftIndicator();
  });
  inspectorBodyEl.querySelector('#open-assessment-config-btn').addEventListener('click', () => openAssessmentSettings());
  inspectorBodyEl.querySelector('#field-slug').addEventListener('input', (event) => {
    state.questionnaire.slug = event.target.value;
    renderTopbar();
  });
  inspectorBodyEl.querySelector('#field-is-disabled').addEventListener('change', (event) => {
    state.questionnaire.is_disabled = event.target.checked;
    updateDraftIndicator();
  });
  const deleteBtn = inspectorBodyEl.querySelector('#delete-questionnaire-btn');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', () => {
      deleteQuestionnaireItem({
        id: state.currentId,
        name: questionnaireDisplayName(state.questionnaire),
      }).catch((error) => showToast(error.message || '删除失败，请稍后重试', true));
    });
  }
}

function renderAssessmentInspector() {
  const builder = ensureAssessmentBuilder();
  if (!editorConfig.defaultAssessment) {
    const group = assessmentTemplateGroups()[0] || null;
    const templates = availableAssessmentTemplates();
    inspectorTitleEl.textContent = group ? '测评模板引用' : '添加多维测评模板';
    inspectorSubtitleEl.textContent = group
      ? '普通问卷只管理这个模板引用，模板内容不在这里改。'
      : '从已创建模板中选择一组题插入当前问卷。';
    const groupDimensions = group
      ? [...new Set((group.questions || []).map((item) => item.assessment_dimension_key).filter(Boolean))]
      : [];
    const templateListHtml = state.loadingList
      ? '<div class="empty-state">正在读取已创建的测评模板...</div>'
      : templates.length
        ? `
          <div class="assessment-template-list">
            ${templates.map((template) => {
              const dimensionCount = assessmentTemplateDimensionCount(template);
              const questionCount = assessmentTemplateQuestionCount(template);
              return `
                <article class="assessment-template-card">
                  <h4>${escapeHtml(assessmentTemplateReferenceName(template))}</h4>
                  <p>${escapeHtml(template.description || '从「创建测评问卷模板」保存出来的测评模板资产。')}</p>
                  <div class="assessment-template-meta">
                    <span class="template-group-chip">${dimensionCount ? `${escapeHtml(String(dimensionCount))} 个维度` : '维度随模板导入'}</span>
                    <span class="template-group-chip">${questionCount ? `${escapeHtml(String(questionCount))} 道题` : '题目点击后读取'}</span>
                    <span class="template-group-chip">含结果页规则</span>
                  </div>
                  <button
                    type="button"
                    class="btn primary"
                    style="width:100%;"
                    data-assessment-template-questionnaire-id="${escapeHtml(String(template.id))}"
                  >添加这个模板到当前问卷</button>
                </article>
              `;
            }).join('')}
          </div>
        `
        : `
          <div class="empty-state">
            还没有可添加的测评模板。先回到问卷管理，点击「创建测评问卷模板」保存一个模板资产，再在这里选择。
          </div>
        `;
    inspectorBodyEl.innerHTML = `
      <section class="config-group">
        <div class="config-head">
          <div>
            <h3>${group ? escapeHtml(group.name) : '选择测评模板资产'}</h3>
            <p>${group ? '已作为整组测评模板插入当前问卷。' : '选择后会整组插入题目、维度、分型和结果页规则。'}</p>
          </div>
        </div>
        ${group
          ? `<div class="helper-note">
              ${escapeHtml(String(groupDimensions.length))} 个维度、${escapeHtml(String((group.questions || []).length))} 道题、含结果页规则。
              手机号、标签和普通题继续复用当前问卷能力；提交后动作与外部推送由“运营配置”维护。
            </div>`
          : templateListHtml}
        <div class="template-group-actions">
          ${group
            ? '<button id="remove-template-group-btn" type="button" class="btn ghost">删除整组模板</button>'
            : ''}
        </div>
      </section>
      <section class="config-group">
        <div class="config-head">
          <div>
            <h3>配置边界</h3>
            <p>这里是普通问卷，不编辑测评模板内部内容。</p>
          </div>
        </div>
        <div class="helper-note">
          普通问卷负责手机号、城市、来源、标签、分享和提交记录；测评模板负责题目、维度、分型和结果页说明。
        </div>
      </section>
    `;
    inspectorBodyEl.querySelectorAll('[data-assessment-template-questionnaire-id]').forEach((button) => {
      button.addEventListener('click', () => {
        button.disabled = true;
        applyAssessmentTemplateFromQuestionnaire(button.dataset.assessmentTemplateQuestionnaireId)
          .catch((error) => {
            button.disabled = false;
            showToast(error.message || '测评模板添加失败，请稍后重试', true);
          });
      });
    });
    inspectorBodyEl.querySelector('#remove-template-group-btn')?.addEventListener('click', () => {
      removeAssessmentTemplateGroup(group.id);
    });
    return;
  }
  inspectorTitleEl.textContent = editorConfig.defaultAssessment ? '测评模板配置' : '维度与结果页';
  inspectorSubtitleEl.textContent = editorConfig.defaultAssessment
    ? '用中文配置模板题目、维度分型和结果页反馈。'
    : '配置当前引用模板的维度、分型优先级和结果页分层。';
  if (!builder.dimensions.length) {
    builder.dimensions = [createAssessmentDimension({}, 0)];
  }
  const selectedKey = state.selection.dimensionKey || builder.dimensions[0]?.key || '';
  const selectedDimension = builder.dimensions.find((item) => item.key === selectedKey) || builder.dimensions[0];
  state.selection.dimensionKey = selectedDimension?.key || '';
  const dimensionNavHtml = builder.dimensions.map((dimension, index) => `
    <button type="button" class="rule-nav-item${dimension.key === state.selection.dimensionKey ? ' active' : ''}" data-assessment-dimension-select="${escapeHtml(dimension.key)}">
      <strong>${escapeHtml(dimension.name || `维度 ${index + 1}`)}</strong>
      <span>${escapeHtml((dimension.types || []).map((type) => type.name).join(' / ') || '未配置分型')}</span>
      <span>${escapeHtml((dimension.levels || []).map((level) => level.title).join(' / ') || '未配置分层')}</span>
    </button>
  `).join('');
  const typeRowsHtml = selectedDimension ? (selectedDimension.types || []).map((type, index) => `
    <div class="assessment-type-row" data-assessment-type-key="${escapeHtml(type.key)}">
      <input data-assessment-type-field="name" data-type-key="${escapeHtml(type.key)}" value="${escapeHtml(type.name)}" placeholder="例如 被动型">
      <input data-assessment-type-field="summary" data-type-key="${escapeHtml(type.key)}" value="${escapeHtml(type.summary)}" placeholder="这个分型的说明">
      <button type="button" class="link-btn danger" data-assessment-type-remove="${escapeHtml(type.key)}" ${selectedDimension.types.length <= 1 ? 'disabled' : ''}>删除</button>
    </div>
  `).join('') : '';
  const levelRowsHtml = selectedDimension ? (selectedDimension.levels || []).map((level) => `
    <div class="assessment-level-row" data-assessment-level-key="${escapeHtml(level.local_key)}">
      <input data-assessment-level-field="min_score" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(String(level.min_score ?? ''))}" placeholder="最低分">
      <input data-assessment-level-field="max_score" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(String(level.max_score ?? ''))}" placeholder="最高分">
      <input data-assessment-level-field="title" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(level.title)}" placeholder="例如 可用">
      <input data-assessment-level-field="summary" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(level.summary || '')}" placeholder="这一层的结果说明">
      <button type="button" class="link-btn danger" data-assessment-level-remove="${escapeHtml(level.local_key)}" ${selectedDimension.levels.length <= 1 ? 'disabled' : ''}>删除</button>
    </div>
  `).join('') : '';
  const overallRowsHtml = (builder.overall_levels || []).map((level) => `
    <div class="assessment-level-row" data-overall-level-key="${escapeHtml(level.local_key)}">
      <input data-overall-level-field="min_score" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(String(level.min_score ?? ''))}" placeholder="最低分">
      <input data-overall-level-field="max_score" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(String(level.max_score ?? ''))}" placeholder="最高分">
      <input data-overall-level-field="title" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(level.title)}" placeholder="例如 成长期">
      <input data-overall-level-field="summary" data-level-key="${escapeHtml(level.local_key)}" value="${escapeHtml(level.summary || '')}" placeholder="这一层的总览说明">
      <button type="button" class="link-btn danger" data-overall-level-remove="${escapeHtml(level.local_key)}" ${builder.overall_levels.length <= 1 ? 'disabled' : ''}>删除</button>
    </div>
  `).join('');

  inspectorBodyEl.innerHTML = `
    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>测评问卷模板</h3>
          <p>${editorConfig.defaultAssessment ? '这里维护可被普通问卷引用的测评模板。' : '从已创建模板中选择，作为一组题插入当前问卷。'}</p>
        </div>
      </div>
      <div class="helper-note">
        ${escapeHtml(preset.title)}：${preset.dimensions.length} 个维度，${preset.questions.length} 道单选题。${editorConfig.defaultAssessment ? '保存后可作为普通问卷的一组测评模板使用。' : '插入后在普通问卷里按整组管理，运营能力由问卷列表的“运营配置”单独维护。'}
      </div>
      <button id="apply-assessment-preset-btn" type="button" class="btn primary" style="width:100%;margin-top:12px;">${editorConfig.defaultAssessment ? '填入小 IP 模板题目' : '添加多维测评模板到当前问卷'}</button>
    </section>

    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>结果页开关</h3>
          <p>开启后，提交成功页会进入测评结果页。</p>
        </div>
      </div>
      <label class="field" style="font-weight:600;">
        <input id="assessment-enabled-toggle" type="checkbox" ${state.questionnaire.assessment_enabled ? 'checked' : ''}> 启用多维测评
      </label>
      <div class="field-grid compact">
        <label class="field">结果页标题
          <input id="assessment-total-score-title" type="text" value="${escapeHtml(builder.total_score_title || '')}" placeholder="例如 小 IP 商业力测评">
        </label>
        <label class="field">优势 / 劣势展示数量
          <input id="assessment-strength-weakness-count" type="number" min="1" step="1" value="${escapeHtml(String(builder.strength_count || 2))}">
        </label>
      </div>
    </section>

    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>维度</h3>
          <p>不限数量。题目会从这里选择归属维度。</p>
        </div>
        <div class="header-actions">
          <button id="load-assessment-template-btn" type="button" class="btn ghost">填入示例模板</button>
          <button id="add-assessment-dimension-btn" type="button" class="btn secondary">添加维度</button>
        </div>
      </div>
      <div class="rule-nav-list">${dimensionNavHtml}</div>
    </section>

    ${selectedDimension ? `
      <section class="config-group">
        <div class="config-head">
          <div>
            <h3>当前维度</h3>
            <p>维度名称会显示在结果页和题目配置下拉里。</p>
          </div>
          <button id="remove-assessment-dimension-btn" type="button" class="link-btn danger" ${builder.dimensions.length <= 1 ? 'disabled' : ''}>删除维度</button>
        </div>
        <label class="field">维度名称
          <input id="assessment-dimension-name" type="text" value="${escapeHtml(selectedDimension.name)}" placeholder="例如 成交能力">
        </label>
        <label class="field">维度说明
          <textarea id="assessment-dimension-summary" placeholder="给结果页看的简短说明">${escapeHtml(selectedDimension.summary || '')}</textarea>
        </label>
      </section>

      <section class="config-group">
        <div class="config-head">
          <div>
            <h3>分型</h3>
            <p>从上到下就是平票优先级。题目选项会从这里选择类型。</p>
          </div>
          <button id="add-assessment-type-btn" type="button" class="btn secondary">添加分型</button>
        </div>
        <div class="assessment-mini-help" style="margin:0 0 8px;">左列填分型名称，右列填结果页说明。</div>
        <div class="assessment-type-grid">${typeRowsHtml}</div>
      </section>

      <section class="config-group">
        <div class="config-head">
          <div>
            <h3>单维分层</h3>
            <p>按这个维度自己的分数判断好与不好，每层可以填写结果页说明。</p>
          </div>
          <button id="add-assessment-level-btn" type="button" class="btn secondary">添加分层</button>
        </div>
        <div class="assessment-level-grid">${levelRowsHtml}</div>
      </section>
    ` : ''}

    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>综合分层说明</h3>
          <p>只用于结果页总览，不决定单维分型。每层都可以单独配置解释文案。</p>
        </div>
        <button id="add-overall-level-btn" type="button" class="btn secondary">添加分层</button>
      </div>
      <div class="assessment-level-grid">${overallRowsHtml}</div>
    </section>
  `;

  inspectorBodyEl.querySelector('#assessment-enabled-toggle').addEventListener('change', (event) => {
    state.questionnaire.assessment_enabled = event.target.checked;
    updateDraftIndicator();
    renderAssessmentSummary();
  });
  inspectorBodyEl.querySelector('#apply-assessment-preset-btn').addEventListener('click', () => {
    applyAssessmentPreset();
  });
  inspectorBodyEl.querySelector('#assessment-total-score-title').addEventListener('input', (event) => {
    builder.total_score_title = event.target.value;
    updateDraftIndicator();
    renderAssessmentSummary();
  });
  inspectorBodyEl.querySelector('#assessment-strength-weakness-count').addEventListener('input', (event) => {
    const count = Math.max(1, Number(event.target.value || 1));
    builder.strength_count = count;
    builder.weakness_count = count;
    updateDraftIndicator();
  });
  inspectorBodyEl.querySelector('#add-assessment-dimension-btn').addEventListener('click', () => {
    const dimension = createAssessmentDimension({ name: `维度 ${builder.dimensions.length + 1}` }, builder.dimensions.length);
    builder.dimensions.push(dimension);
    state.selection = { kind: 'assessment', dimensionKey: dimension.key };
    renderWorkspace();
  });
  inspectorBodyEl.querySelector('#load-assessment-template-btn').addEventListener('click', () => {
    state.questionnaire.assessment_enabled = true;
    state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(buildDefaultAssessmentConfig());
    state.selection = { kind: 'assessment', dimensionKey: state.questionnaire.assessment_builder.dimensions[0]?.key || '' };
    renderWorkspace();
  });
  inspectorBodyEl.querySelectorAll('[data-assessment-dimension-select]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selection = { kind: 'assessment', dimensionKey: button.dataset.assessmentDimensionSelect };
      renderWorkspace();
    });
  });
  const removeDimensionBtn = inspectorBodyEl.querySelector('#remove-assessment-dimension-btn');
  if (removeDimensionBtn && selectedDimension) {
    removeDimensionBtn.addEventListener('click', () => {
      builder.dimensions = builder.dimensions.filter((item) => item.key !== selectedDimension.key);
      state.questionnaire.questions.forEach((question) => {
        if (question.assessment_dimension_key === selectedDimension.key) {
          question.assessment_dimension_key = '';
          question.options.forEach((option) => { option.assessment_type_key = ''; });
        }
      });
      state.selection = { kind: 'assessment', dimensionKey: builder.dimensions[0]?.key || '' };
      renderWorkspace();
    });
  }
  const dimensionNameInput = inspectorBodyEl.querySelector('#assessment-dimension-name');
  const dimensionSummaryInput = inspectorBodyEl.querySelector('#assessment-dimension-summary');
  if (dimensionNameInput && selectedDimension) {
    dimensionNameInput.addEventListener('input', (event) => {
      selectedDimension.name = event.target.value;
      renderAssessmentSummary();
      updateDraftIndicator();
    });
  }
  if (dimensionSummaryInput && selectedDimension) {
    dimensionSummaryInput.addEventListener('input', (event) => {
      selectedDimension.summary = event.target.value;
      updateDraftIndicator();
    });
  }
  const addTypeBtn = inspectorBodyEl.querySelector('#add-assessment-type-btn');
  if (addTypeBtn && selectedDimension) {
    addTypeBtn.addEventListener('click', () => {
      selectedDimension.types.push(createAssessmentType({ name: `分型 ${selectedDimension.types.length + 1}` }, selectedDimension.types.length));
      renderWorkspace();
    });
  }
  inspectorBodyEl.querySelectorAll('[data-assessment-type-field]').forEach((input) => {
    input.addEventListener('input', (event) => {
      const type = selectedDimension?.types.find((item) => item.key === input.dataset.typeKey);
      if (!type) return;
      type[event.target.dataset.assessmentTypeField] = event.target.value;
      updateDraftIndicator();
      renderPreview();
    });
  });
  inspectorBodyEl.querySelectorAll('[data-assessment-type-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!selectedDimension || selectedDimension.types.length <= 1) return;
      selectedDimension.types = selectedDimension.types.filter((item) => item.key !== button.dataset.assessmentTypeRemove);
      state.questionnaire.questions.forEach((question) => {
        if (question.assessment_dimension_key !== selectedDimension.key) return;
        question.options.forEach((option) => {
          if (option.assessment_type_key === button.dataset.assessmentTypeRemove) option.assessment_type_key = '';
        });
      });
      renderWorkspace();
    });
  });
  const addLevelBtn = inspectorBodyEl.querySelector('#add-assessment-level-btn');
  if (addLevelBtn && selectedDimension) {
    addLevelBtn.addEventListener('click', () => {
      selectedDimension.levels.push(createAssessmentLevel({ title: `分层 ${selectedDimension.levels.length + 1}` }, selectedDimension.levels.length));
      renderWorkspace();
    });
  }
  inspectorBodyEl.querySelectorAll('[data-assessment-level-field]').forEach((input) => {
    input.addEventListener('input', (event) => {
      const level = selectedDimension?.levels.find((item) => item.local_key === input.dataset.levelKey);
      if (!level) return;
      level[event.target.dataset.assessmentLevelField] = event.target.value;
      updateDraftIndicator();
    });
  });
  inspectorBodyEl.querySelectorAll('[data-assessment-level-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!selectedDimension || selectedDimension.levels.length <= 1) return;
      selectedDimension.levels = selectedDimension.levels.filter((item) => item.local_key !== button.dataset.assessmentLevelRemove);
      renderWorkspace();
    });
  });
  inspectorBodyEl.querySelector('#add-overall-level-btn').addEventListener('click', () => {
    builder.overall_levels.push(createAssessmentLevel({ title: `综合分层 ${builder.overall_levels.length + 1}` }, builder.overall_levels.length));
    renderWorkspace();
  });
  inspectorBodyEl.querySelectorAll('[data-overall-level-field]').forEach((input) => {
    input.addEventListener('input', (event) => {
      const level = builder.overall_levels.find((item) => item.local_key === input.dataset.levelKey);
      if (!level) return;
      level[event.target.dataset.overallLevelField] = event.target.value;
      updateDraftIndicator();
    });
  });
  inspectorBodyEl.querySelectorAll('[data-overall-level-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      if (builder.overall_levels.length <= 1) return;
      builder.overall_levels = builder.overall_levels.filter((item) => item.local_key !== button.dataset.overallLevelRemove);
      renderWorkspace();
    });
  });
}

function renderAssessmentTemplateGroupInspector(group) {
  const builder = ensureAssessmentBuilder();
  const groupDimensions = [...new Set((group?.questions || []).map((item) => item.assessment_dimension_key).filter(Boolean))];
  inspectorTitleEl.textContent = '测评模板引用';
  inspectorSubtitleEl.textContent = '普通问卷只管理这个模板引用，模板内容不在这里改。';
  inspectorBodyEl.innerHTML = `
    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>${escapeHtml(group?.name || DEFAULT_ASSESSMENT_TEMPLATE_NAME)}</h3>
          <p>已作为整组测评模板插入当前问卷。</p>
        </div>
      </div>
      <div class="helper-note">
        ${escapeHtml(String(groupDimensions.length))} 个维度、${escapeHtml(String((group?.questions || []).length))} 道题、${escapeHtml(String((builder.overall_levels || []).length))} 个综合分层。题目来自同一模板，当前问卷里只能整组保留或整组删除。
      </div>
      <div class="template-group-actions">
        <button id="remove-template-group-btn" type="button" class="btn ghost">删除整组模板</button>
      </div>
    </section>
    <section class="config-group">
      <div class="config-head">
        <div>
          <h3>配置边界</h3>
          <p>普通问卷能力继续复用。</p>
        </div>
      </div>
      <div class="helper-note">手机号、城市、来源渠道、标签、分享链接和提交记录都留在普通问卷里处理。如果需要改题目、分型或结果页文案，请回到测评模板页维护。</div>
    </section>
  `;
  inspectorBodyEl.querySelector('#remove-template-group-btn').addEventListener('click', () => {
    removeAssessmentTemplateGroup(group.id);
  });
}

function renderQuestionInspector(question) {
  inspectorTitleEl.textContent = editorConfig.defaultAssessment ? '配置区' : formatQuestionType(question.type);
  inspectorSubtitleEl.textContent = editorConfig.defaultAssessment
    ? '右侧只使用中文可选项，内部字段由系统生成。'
    : '编辑题目内容、选项与分值。';
  const dimensions = assessmentDimensions();
  const selectedDimension = getAssessmentDimensionByKey(question.assessment_dimension_key);
  const dimensionOptionsHtml = [
    `<option value="">${editorConfig.defaultAssessment ? '请选择维度' : '不计入测评'}</option>`,
    ...dimensions.map((dimension) => `
      <option value="${escapeHtml(dimension.key)}" ${dimension.key === question.assessment_dimension_key ? 'selected' : ''}>${escapeHtml(dimension.name)}</option>
    `),
  ].join('');
  const optionsHtml = ['textarea', 'mobile'].includes(question.type)
    ? ''
    : (question.options || []).map((option, index) => {
        const optionTypeOptionsHtml = selectedDimension
          ? [
              '<option value="">请选择分型</option>',
              ...(selectedDimension.types || []).map((type) => `
                <option value="${escapeHtml(type.key)}" ${type.key === option.assessment_type_key ? 'selected' : ''}>${escapeHtml(type.name)}</option>
              `),
            ].join('')
          : '<option value="">先选择测评维度</option>';
        const otherMaxLength = normalizeOtherMaxLength(option.other_max_length);
        return `
          <div class="option-editor" data-option-key="${escapeHtml(option.local_key)}" draggable="true">
          <div class="option-editor-head">
            <span class="mini-label" title="拖拽调整顺序">⋮⋮ 选项 ${index + 1}</span>
            <button type="button" class="link-btn danger remove-option-btn" data-option-key="${escapeHtml(option.local_key)}">删除</button>
          </div>
          <div class="field-grid triple">
            <label class="field">选项文案
              <input data-option-field="option_text" data-option-key="${escapeHtml(option.local_key)}" type="text" value="${escapeHtml(option.option_text)}">
            </label>
            <label class="field">分值
              ${editorConfig.defaultAssessment
                ? `<select data-option-field="score" data-option-key="${escapeHtml(option.local_key)}">
                    ${[1, 2, 3, 4].map((score) => `<option value="${score}" ${Number(option.score || 0) === score ? 'selected' : ''}>${score} 分</option>`).join('')}
                  </select>`
                : `<input data-option-field="score" data-option-key="${escapeHtml(option.local_key)}" type="text" value="${escapeHtml(String(option.score ?? 0))}">`}
            </label>
            <label class="field">${editorConfig.defaultAssessment ? '测评类型' : '测评分型'}
              <select data-option-field="assessment_type_key" data-option-key="${escapeHtml(option.local_key)}" ${selectedDimension ? '' : 'disabled'}>
                ${optionTypeOptionsHtml}
              </select>
            </label>
          </div>
          <label class="field" style="margin-bottom:8px;">
            <input
              data-option-field="is_other"
              data-option-key="${escapeHtml(option.local_key)}"
              type="checkbox"
              ${option.is_other ? 'checked' : ''}
            > 设为其它选项
          </label>
          <div class="option-other-config${option.is_other ? '' : ' hidden'}">
            <label class="field">输入框提示文案
              <input
                data-option-field="other_placeholder"
                data-option-key="${escapeHtml(option.local_key)}"
                type="text"
                value="${escapeHtml(option.other_placeholder || '')}"
                placeholder="请填写其它内容"
              >
            </label>
            <label class="field">最多输入字数
              <input
                data-option-field="other_max_length"
                data-option-key="${escapeHtml(option.local_key)}"
                type="number"
                min="1"
                max="200"
                step="1"
                value="${escapeHtml(String(otherMaxLength))}"
              >
            </label>
          </div>
          <div class="tag-picker-host" data-option-tag-host="${escapeHtml(option.local_key)}"></div>
        </div>
        `;
      }).join('');
  inspectorBodyEl.innerHTML = `
    <section class="config-group">
      <div class="config-head">
        <div><h3>题目</h3></div>
        <button id="remove-question-btn" type="button" class="link-btn danger">删除</button>
      </div>
      <label class="field">题型
        <select id="question-type">
          ${editorConfig.defaultAssessment
            ? '<option value="single_choice">单选测评题</option>'
            : '<option value="single_choice">单选题</option><option value="multi_choice">多选题</option><option value="textarea">文本题</option><option value="mobile">手机号题</option>'}
        </select>
      </label>
      <label class="field">${editorConfig.defaultAssessment ? '题目标题' : '题目标题'}
        ${editorConfig.defaultAssessment
          ? `<textarea id="question-title">${escapeHtml(question.title)}</textarea>`
          : `<input id="question-title" type="text" value="${escapeHtml(question.title)}">`}
      </label>
      <label class="field">${editorConfig.defaultAssessment ? '所属维度' : '测评维度'}
        <select id="question-assessment-dimension-key">
          ${dimensionOptionsHtml}
        </select>
        <div class="assessment-mini-help">启用多维测评后，此题只会计入选中的维度。维度可在「维度与分型」里增删。</div>
      </label>
      <label class="field${['textarea', 'mobile'].includes(question.type) ? '' : ' hidden'}" id="question-placeholder-field">提示文字
        <input
          id="question-placeholder-text"
          type="text"
          value="${escapeHtml(question.placeholder_text || '')}"
          placeholder="${escapeHtml(question.type === 'mobile' ? '例如：请输入手机号' : '例如：写下你的目标')}"
        >
      </label>
      <label class="field" style="margin-bottom:0;">
        <input id="question-required" type="checkbox" ${question.required ? 'checked' : ''}> 必填
      </label>
    </section>
    <section class="config-group">
      <div class="config-head">
        <div><h3>侧边栏核心画像映射</h3></div>
      </div>
      <label class="field">同步字段
        <select id="question-sidebar-profile-field">
          ${sidebarProfileFieldOptionsHtml(question.sidebar_profile_field)}
        </select>
      </label>
      <p>用户填什么就同步什么；多个题目映射同一字段时，后填写覆盖前填写。</p>
    </section>
    <section class="config-group${['textarea', 'mobile'].includes(question.type) ? ' hidden' : ''}" id="question-options-group">
      <div class="config-head">
        <div><h3>选项</h3></div>
        <button id="add-option-btn" type="button" class="btn secondary">添加选项</button>
      </div>
      ${optionsHtml || '<div class="empty-state">点击"添加选项"开始配置</div>'}
    </section>
  `;
  inspectorBodyEl.querySelector('#question-type').value = question.type;

  inspectorBodyEl.querySelector('#question-title').addEventListener('input', (event) => {
    question.title = event.target.value;
    renderPreview();
  });
  inspectorBodyEl.querySelector('#question-assessment-dimension-key').addEventListener('input', (event) => {
    question.assessment_dimension_key = event.target.value;
    question.options.forEach((option) => { option.assessment_type_key = ''; });
    renderWorkspace();
  });
  const questionPlaceholderInput = inspectorBodyEl.querySelector('#question-placeholder-text');
  if (questionPlaceholderInput) {
    questionPlaceholderInput.addEventListener('input', (event) => {
      question.placeholder_text = event.target.value;
      renderPreview();
    });
  }
  inspectorBodyEl.querySelector('#question-required').addEventListener('change', (event) => {
    question.required = event.target.checked;
    renderPreview();
  });
  inspectorBodyEl.querySelector('#question-sidebar-profile-field').addEventListener('change', (event) => {
    question.sidebar_profile_field = normalizeSidebarProfileField(event.target.value);
    renderPreview();
  });
  inspectorBodyEl.querySelector('#question-type').addEventListener('change', (event) => {
    question.type = event.target.value;
    if (['textarea', 'mobile'].includes(question.type)) {
      question.options = [];
    } else if (!question.options.length) {
      question.options = [createOption({}, 0)];
    }
    renderWorkspace();
  });
  inspectorBodyEl.querySelector('#remove-question-btn').addEventListener('click', () => {
    state.questionnaire.questions = state.questionnaire.questions.filter((item) => item.local_key !== question.local_key);
    state.selection = { kind: 'questionnaire' };
    renderWorkspace();
  });

  const addOptionBtn = inspectorBodyEl.querySelector('#add-option-btn');
  if (addOptionBtn) {
    addOptionBtn.addEventListener('click', () => {
      question.options.push(createOption({}, question.options.length));
      renderWorkspace();
    });
  }

  inspectorBodyEl.querySelectorAll('[data-option-field]').forEach((input) => {
    const updateOptionField = (event) => {
      const option = question.options.find((item) => item.local_key === event.target.dataset.optionKey);
      if (!option) return;
      const field = event.target.dataset.optionField;
      if (field === 'is_other') {
        option.is_other = event.target.checked;
        if (option.is_other) {
          question.options.forEach((item) => {
            if (item.local_key !== option.local_key) item.is_other = false;
          });
          if (!option.option_text) option.option_text = '其它';
          if (!option.other_max_length) option.other_max_length = 80;
        }
        renderWorkspace();
        return;
      }
      option[field] = field === 'other_max_length'
        ? (event.target.value === '' ? '' : Number(event.target.value))
        : event.target.value;
      renderPreview();
    };
    input.addEventListener('input', updateOptionField);
    input.addEventListener('change', updateOptionField);
  });
  inspectorBodyEl.querySelectorAll('.remove-option-btn').forEach((button) => {
    button.addEventListener('click', () => {
      question.options = question.options.filter((item) => item.local_key !== button.dataset.optionKey);
      if (!question.options.length && !['textarea', 'mobile'].includes(question.type)) {
        question.options = [createOption({}, 0)];
      }
      renderWorkspace();
    });
  });
  question.options.forEach((option) => {
    const host = inspectorBodyEl.querySelector(`[data-option-tag-host="${option.local_key}"]`);
    if (!host) return;
    const apply = (tagIds) => {
      option.tag_codes = tagIds;
    };
    apply.currentValue = () => option.tag_codes;
    mountTagPicker(host, option.tag_codes, apply, {
      type: 'option',
      questionKey: question.local_key,
      optionKey: option.local_key,
    });
  });

  let dragKey = null;
  inspectorBodyEl.querySelectorAll('.option-editor[draggable="true"]').forEach((node) => {
    node.addEventListener('dragstart', (event) => {
      dragKey = node.dataset.optionKey;
      node.classList.add('is-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', dragKey || '');
      }
    });
    node.addEventListener('dragend', () => {
      dragKey = null;
      node.classList.remove('is-dragging');
    });
    node.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
      node.classList.add('is-drop-target');
    });
    node.addEventListener('dragleave', () => {
      node.classList.remove('is-drop-target');
    });
    node.addEventListener('drop', (event) => {
      event.preventDefault();
      node.classList.remove('is-drop-target');
      const sourceKey = dragKey || (event.dataTransfer && event.dataTransfer.getData('text/plain'));
      const targetKey = node.dataset.optionKey;
      if (!sourceKey || !targetKey || sourceKey === targetKey) return;
      const items = [...question.options];
      const fromIdx = items.findIndex((item) => item.local_key === sourceKey);
      const toIdx = items.findIndex((item) => item.local_key === targetKey);
      if (fromIdx < 0 || toIdx < 0) return;
      const [moved] = items.splice(fromIdx, 1);
      items.splice(toIdx, 0, moved);
      question.options = items;
      renderWorkspace();
      updateDraftIndicator();
    });
  });
}

function renderRuleInspector(rule) {
  inspectorTitleEl.textContent = '分数规则';
  inspectorSubtitleEl.textContent = '按总分区间给提交者打标签。';
  const ruleListHtml = state.questionnaire.score_rules.length
    ? `
      <div class="rule-nav-list">
        ${state.questionnaire.score_rules.map((item, index) => `
          <button type="button" class="rule-nav-item${rule && item.local_key === rule.local_key ? ' active' : ''}" data-rule-key="${escapeHtml(item.local_key)}">
            <strong>规则 ${index + 1}</strong>
            <span>${escapeHtml(String(item.min_score ?? ''))} - ${escapeHtml(String(item.max_score ?? ''))}</span>
            <span>${normalizeTagIds(item.tag_codes).length ? normalizeTagIds(item.tag_codes).map((tagId) => formatTagLabel(ensureTagKnown(tagId))).join(' / ') : '未选择标签'}</span>
          </button>
        `).join('')}
      </div>
    `
    : `
      <div class="empty-state">
        <strong style="display:block;font-size:18px;margin-bottom:6px;">当前还没有分数规则</strong>
        <div class="muted" style="margin-bottom:14px;">点击下方按钮新增规则</div>
        <button id="empty-add-rule-btn" type="button" class="btn secondary">新增规则</button>
      </div>
    `;
  const editorHtml = rule ? `
    <section class="config-group">
      <div class="config-head">
        <div><h3>规则设置</h3></div>
        <button id="remove-rule-btn" type="button" class="link-btn danger">删除</button>
      </div>
      <div class="field-grid compact">
        <label class="field">最低分
          <input id="rule-min-score" type="text" value="${escapeHtml(String(rule.min_score ?? ''))}">
        </label>
        <label class="field">最高分
          <input id="rule-max-score" type="text" value="${escapeHtml(String(rule.max_score ?? ''))}">
        </label>
      </div>
      <div id="rule-tag-host"></div>
    </section>
  ` : `
    <section class="config-group">
      <div class="config-head"><div><h3>规则设置</h3></div></div>
      <div class="helper-note">从上方选择一条规则，或新增规则。</div>
    </section>
  `;
  inspectorBodyEl.innerHTML = `
    <section class="config-group">
      <div class="config-head">
        <div><h3>规则列表</h3></div>
        <button id="rule-list-add-btn" type="button" class="btn secondary">新增</button>
      </div>
      ${ruleListHtml}
    </section>
    ${editorHtml}
  `;
  inspectorBodyEl.querySelector('#rule-list-add-btn').addEventListener('click', () => addRule());
  inspectorBodyEl.querySelectorAll('[data-rule-key]').forEach((button) => {
    button.addEventListener('click', () => selectRule(button.dataset.ruleKey));
  });
  const emptyAddRuleBtn = inspectorBodyEl.querySelector('#empty-add-rule-btn');
  if (emptyAddRuleBtn) {
    emptyAddRuleBtn.addEventListener('click', () => addRule());
  }
  if (!rule) {
    return;
  }
  inspectorBodyEl.querySelector('#rule-min-score').addEventListener('input', (event) => {
    rule.min_score = event.target.value;
    renderPreview();
  });
  inspectorBodyEl.querySelector('#rule-max-score').addEventListener('input', (event) => {
    rule.max_score = event.target.value;
    renderPreview();
  });
  inspectorBodyEl.querySelector('#remove-rule-btn').addEventListener('click', () => {
    const currentIndex = state.questionnaire.score_rules.findIndex((item) => item.local_key === rule.local_key);
    state.questionnaire.score_rules = state.questionnaire.score_rules.filter((item) => item.local_key !== rule.local_key);
    state.ruleMode = true;
    if (state.questionnaire.score_rules.length) {
      const nextIndex = Math.min(currentIndex, state.questionnaire.score_rules.length - 1);
      state.lastRuleKey = state.questionnaire.score_rules[nextIndex].local_key;
      state.selection = { kind: 'rule', key: state.questionnaire.score_rules[nextIndex].local_key };
    } else {
      state.lastRuleKey = '';
      state.selection = { kind: 'questionnaire' };
    }
    renderWorkspace();
  });
  const apply = (tagIds) => {
    rule.tag_codes = tagIds;
    renderPreview();
  };
  apply.currentValue = () => rule.tag_codes;
  mountTagPicker(inspectorBodyEl.querySelector('#rule-tag-host'), rule.tag_codes, apply, {
    type: 'rule',
    ruleKey: rule.local_key,
  });
}

const ASSESSMENT_TEMPLATE_STEPS = [
  { key: 'basic', label: '基础信息' },
  { key: 'dimensions', label: '设置维度' },
  { key: 'results', label: '结果配置' },
  { key: 'preview', label: 'H5 预览 / 发布' },
];

function setAssessmentTemplateStep(step) {
  state.assessmentStep = step;
  state.selection = { kind: 'assessment' };
  renderWorkspace();
}

function ensureAssessmentEditorState() {
  const builder = ensureAssessmentBuilder();
  const dimensions = builder.dimensions || [];
  if (!dimensions.some((dimension) => dimension.key === state.selectedDimensionKey)) {
    state.selectedDimensionKey = dimensions[0]?.key || '';
  }
  const currentDimension = dimensions.find((dimension) => dimension.key === state.selectedDimensionKey) || null;
  const questions = currentDimension
    ? state.questionnaire.questions.filter((question) => question.assessment_dimension_key === currentDimension.key)
    : [];
  if (!questions.some((question) => question.local_key === state.selectedQuestionKey)) {
    state.selectedQuestionKey = questions[0]?.local_key || '';
  }
  const types = currentDimension?.types || [];
  if (!types.some((type) => type.key === state.selectedAssessmentTypeKey)) {
    state.selectedAssessmentTypeKey = types[0]?.key || '';
  }
  const levels = builder.overall_levels || [];
  if (!levels.some((level) => level.local_key === state.selectedOverallLevelKey)) {
    state.selectedOverallLevelKey = levels[0]?.local_key || '';
  }
  if (!state.assessmentResultTab) state.assessmentResultTab = 'dimension';
  if (!state.assessmentPreviewMode) state.assessmentPreviewMode = 'full';
  return { builder, dimensions, currentDimension, questions };
}

function selectedAssessmentDimension() {
  const builder = ensureAssessmentBuilder();
  return (builder.dimensions || []).find((dimension) => dimension.key === state.selectedDimensionKey) || null;
}

function selectedAssessmentType() {
  const dimension = selectedAssessmentDimension();
  return (dimension?.types || []).find((type) => type.key === state.selectedAssessmentTypeKey) || null;
}

function selectedOverallLevel() {
  const builder = ensureAssessmentBuilder();
  return (builder.overall_levels || []).find((level) => level.local_key === state.selectedOverallLevelKey) || null;
}

function questionsForDimension(dimensionKey) {
  return (state.questionnaire.questions || []).filter((question) => question.assessment_dimension_key === dimensionKey);
}

function optionCountForDimension(dimensionKey) {
  return questionsForDimension(dimensionKey).reduce((total, question) => total + (question.options || []).length, 0);
}

function dimensionConfigStatus(dimension) {
  if (!dimension) return { label: '待补充', className: 'warn' };
  const questionCount = questionsForDimension(dimension.key).length;
  const hasTypes = (dimension.types || []).length > 0;
  const hasResultCopy = (dimension.types || []).some((type) => (
    type.greeting || type.diagnosis || type.summary || type.recommended_action || type.course_url
  ));
  if (!questionCount) return { label: '待补题', className: 'warn' };
  if (!hasTypes || !hasResultCopy) return { label: '缺结果', className: 'warn' };
  return { label: '已配置', className: 'ok' };
}

function uniqueAssessmentKey(base, existingKeys) {
  const raw = String(base || '').trim() || 'dimension';
  let normalized = raw.replace(/\s+/g, '_').replace(/[^\w\u4e00-\u9fa5-]/g, '');
  if (!normalized) normalized = 'dimension';
  let candidate = normalized;
  let index = 2;
  while (existingKeys.includes(candidate)) {
    candidate = `${normalized}_${index}`;
    index += 1;
  }
  return candidate;
}

function renderAssessmentStepNav() {
  const sidebarCard = document.querySelector('.sidebar .section-card');
  if (!sidebarCard) return;
  sidebarCard.innerHTML = `
    <div class="section-head">
      <div>
        <h3>配置步骤</h3>
        <p class="section-subtitle">先配置维度模型，再配置结果规则，最后统一预览 H5。</p>
      </div>
    </div>
    <div class="template-step-list">
      ${ASSESSMENT_TEMPLATE_STEPS.map((step, index) => `
        <button type="button" class="template-step${state.assessmentStep === step.key ? ' active' : ''}" data-assessment-step="${escapeHtml(step.key)}">
          <span class="template-step-num">${index + 1}</span>
          <span>${escapeHtml(step.label)}</span>
        </button>
      `).join('')}
    </div>
    <div class="assessment-template-note">这里创建的是“测评模板资产”。普通问卷只能整组引用模板，不在普通问卷里编辑模板内部题目、维度和结果页。</div>
  `;
  sidebarCard.querySelectorAll('[data-assessment-step]').forEach((button) => {
    button.addEventListener('click', () => setAssessmentTemplateStep(button.dataset.assessmentStep));
  });
}

function setInspectorContent(title, subtitle, bodyHtml) {
  inspectorTitleEl.textContent = title;
  inspectorSubtitleEl.textContent = subtitle;
  inspectorBodyEl.innerHTML = bodyHtml;
}

function renderAssessmentBasicPage() {
  const phoneStage = document.querySelector('.phone-stage');
  phoneStage.innerHTML = `
    <div class="template-page">
      <div class="template-page-head">
        <div>
          <h2>基础信息</h2>
          <p>只配置测评基础展示信息。这里不放题目、不放结果规则。</p>
        </div>
      </div>
      <div class="template-panel" style="max-width:880px;">
        <div class="template-panel-head"><h3>测评基础配置</h3></div>
        <div class="template-panel-body">
          <label class="field">问卷名称
            <input id="v2-basic-name" type="text" value="${escapeHtml(state.questionnaire.name)}">
          </label>
          <label class="field">问卷标题
            <input id="v2-basic-title" type="text" value="${escapeHtml(state.questionnaire.title)}">
          </label>
          <label class="field">副标题 / 简介
            <textarea id="v2-basic-description">${escapeHtml(state.questionnaire.description)}</textarea>
          </label>
          <div class="field-grid compact">
            <label class="field">分享标识
              <input id="v2-basic-slug" type="text" value="${escapeHtml(state.questionnaire.slug)}" placeholder="保存时可自动生成">
            </label>
            <label class="field"><span style="display:block;margin-bottom:7px;">是否停用</span>
              <label class="field" style="margin-bottom:0;font-weight:600;"><input id="v2-basic-disabled" type="checkbox" ${state.questionnaire.is_disabled ? 'checked' : ''}> 停用问卷</label>
            </label>
          </div>
          <label class="field">答题展示方式
            <select id="v2-basic-answer-display-mode">
              <option value="all_in_one" ${normalizeAnswerDisplayMode(state.questionnaire.answer_display_mode) === 'all_in_one' ? 'selected' : ''}>整页答题</option>
              <option value="one_by_one" ${normalizeAnswerDisplayMode(state.questionnaire.answer_display_mode) === 'one_by_one' ? 'selected' : ''}>一题一页</option>
            </select>
          </label>
        </div>
      </div>
    </div>
  `;
  phoneStage.querySelector('#v2-basic-name').addEventListener('input', (event) => {
    state.questionnaire.name = event.target.value;
    renderTopbar();
  });
  phoneStage.querySelector('#v2-basic-title').addEventListener('input', (event) => {
    state.questionnaire.title = event.target.value;
    renderTopbar();
  });
  phoneStage.querySelector('#v2-basic-description').addEventListener('input', (event) => {
    state.questionnaire.description = event.target.value;
    updateDraftIndicator();
  });
  phoneStage.querySelector('#v2-basic-slug').addEventListener('input', (event) => {
    state.questionnaire.slug = event.target.value;
    updateDraftIndicator();
  });
  phoneStage.querySelector('#v2-basic-disabled').addEventListener('change', (event) => {
    state.questionnaire.is_disabled = event.target.checked;
    updateDraftIndicator();
  });
  phoneStage.querySelector('#v2-basic-answer-display-mode').addEventListener('change', (event) => {
    state.questionnaire.answer_display_mode = normalizeAnswerDisplayMode(event.target.value);
    updateDraftIndicator();
  });
  setInspectorContent(
    '基础信息',
    '正式编辑流程不展示填写模板案例。',
    `<section class="config-group">
      <div class="helper-note">题型只是题目属性；维度、题目和结果规则分别在后续步骤维护。</div>
      <button type="button" class="btn primary" data-next-step="dimensions" style="width:100%;margin-top:12px;">下一步：设置维度</button>
    </section>`,
  );
  inspectorBodyEl.querySelector('[data-next-step]').addEventListener('click', () => setAssessmentTemplateStep('dimensions'));
}

function renderDimensionCards(dimensions) {
  if (!dimensions.length) {
    return '<div class="empty-state">当前还没有维度。点击“添加维度”后再配置题目。</div>';
  }
  return dimensions.map((dimension, index) => {
    const status = dimensionConfigStatus(dimension);
    return `
      <button type="button" class="dimension-card-v2${dimension.key === state.selectedDimensionKey ? ' active' : ''}" data-dimension-key="${escapeHtml(dimension.key)}">
        <div class="dimension-card-title">
          <span>${escapeHtml(dimension.name || `维度 ${index + 1}`)}</span>
          <span class="template-status ${status.className}">${escapeHtml(status.label)}</span>
        </div>
        <div class="template-stat-row">
          <span>${escapeHtml(String(questionsForDimension(dimension.key).length))} 道题</span>
          <span>${escapeHtml(String(optionCountForDimension(dimension.key)))} 个选项</span>
          <span>${dimension.enabled === false ? '停用' : '启用'}</span>
        </div>
      </button>
    `;
  }).join('');
}

function renderQuestionEditor(question, index, dimension) {
  const typeOptions = ['single_choice', 'multi_choice', 'textarea', 'mobile'].map((type) => `
    <option value="${type}" ${question.type === type ? 'selected' : ''}>${formatQuestionType(type)}</option>
  `).join('');
  const optionRows = ['textarea', 'mobile'].includes(question.type)
    ? '<div class="empty-state">文本题和手机号题没有选项。</div>'
    : (question.options || []).map((option, optionIndex) => {
        const typeOptionsHtml = (dimension?.types || []).map((type) => `
          <option value="${escapeHtml(type.key)}" ${type.key === option.assessment_type_key ? 'selected' : ''}>${escapeHtml(type.name)}</option>
        `).join('');
        return `
          <div class="option-row-v2" data-option-key="${escapeHtml(option.local_key)}">
            <input data-option-field="option_text" value="${escapeHtml(option.option_text)}" placeholder="选项文案">
            <input data-option-field="score" type="number" step="1" value="${escapeHtml(String(option.score ?? 0))}" placeholder="分值">
            <select data-option-field="assessment_type_key">
              <option value="">未分类</option>
              ${typeOptionsHtml}
            </select>
            <button type="button" class="link-btn danger" data-remove-option="${escapeHtml(option.local_key)}">删除</button>
          </div>
        `;
      }).join('');
  return `
    <article class="question-card-v2${question.local_key === state.selectedQuestionKey ? ' selected' : ''}" data-question-key="${escapeHtml(question.local_key)}">
      <div class="question-card-head-v2">
        <div>
          <div class="template-muted">Q${String(index + 1).padStart(2, '0')} / ${escapeHtml(dimension?.name || '未分组维度')}</div>
          <input class="question-title-input" data-question-field="title" value="${escapeHtml(question.title)}" placeholder="题目标题">
        </div>
        <div class="question-card-actions">
          <button type="button" class="btn ghost" data-move-question="${escapeHtml(question.local_key)}" data-direction="-1">上移</button>
          <button type="button" class="btn ghost" data-move-question="${escapeHtml(question.local_key)}" data-direction="1">下移</button>
          <button type="button" class="link-btn danger" data-remove-question="${escapeHtml(question.local_key)}">删除</button>
        </div>
      </div>
      <div class="template-panel-body">
        <div class="field-grid compact">
          <label class="field">题型
            <select data-question-field="type">${typeOptions}</select>
          </label>
          <label class="field"><span style="display:block;margin-bottom:7px;">是否必填</span>
            <label class="field" style="font-weight:600;"><input data-question-field="required" type="checkbox" ${question.required ? 'checked' : ''}> 必填</label>
          </label>
        </div>
        <label class="field">题目说明 / 占位提示
          <input data-question-field="placeholder_text" value="${escapeHtml(question.placeholder_text || '')}" placeholder="可选">
        </label>
        <section class="config-group" style="margin:0 0 12px;">
          <div class="config-head">
            <div><h3>侧边栏核心画像映射</h3></div>
          </div>
          <label class="field">同步字段
            <select data-question-field="sidebar_profile_field">${sidebarProfileFieldOptionsHtml(question.sidebar_profile_field)}</select>
          </label>
          <p>用户填什么就同步什么；多个题目映射同一字段时，后填写覆盖前填写。</p>
        </section>
        <div class="config-head" style="margin:8px 0;">
          <div><h4 style="margin:0;font-size:13px;">选项</h4></div>
          <button type="button" class="btn secondary" data-add-option="${escapeHtml(question.local_key)}" ${['textarea', 'mobile'].includes(question.type) ? 'disabled' : ''}>添加选项</button>
        </div>
        ${optionRows}
      </div>
    </article>
  `;
}

function renderDimensionSettingsForm(currentDimension) {
  if (!currentDimension) return '<div class="empty-state">选择维度后在这里编辑。</div>';
  return `
    <section class="config-group">
      <label class="field">维度名称<input id="v2-dim-name" value="${escapeHtml(currentDimension.name)}"></label>
      <label class="field">维度说明<textarea id="v2-dim-summary">${escapeHtml(currentDimension.summary || '')}</textarea></label>
      <div class="field-grid compact">
        <label class="field">维度权重<input id="v2-dim-weight" type="number" step="1" value="${escapeHtml(String(currentDimension.weight ?? ''))}"></label>
        <label class="field">计分方式<select id="v2-dim-scoring"><option value="sum" ${currentDimension.scoring_method === 'sum' ? 'selected' : ''}>选项分值累加</option></select></label>
      </div>
      <label class="field">维度内分类方式<select id="v2-dim-category-method"><option value="most_selected" ${currentDimension.category_method === 'most_selected' ? 'selected' : ''}>按选中分类出现次数最多</option></select></label>
      <label class="field"><input id="v2-dim-enabled" type="checkbox" ${currentDimension.enabled !== false ? 'checked' : ''}> 启用维度</label>
      <label class="field"><input id="v2-dim-total" type="checkbox" ${currentDimension.participates_in_total_score !== false ? 'checked' : ''}> 参与总分</label>
      <label class="field"><input id="v2-dim-show" type="checkbox" ${currentDimension.show_in_result !== false ? 'checked' : ''}> 参与结果页展示</label>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button type="button" class="btn ghost" id="v2-move-dimension-up">上移</button>
        <button type="button" class="btn ghost" id="v2-move-dimension-down">下移</button>
        <button type="button" class="link-btn danger" id="v2-delete-dimension">删除维度</button>
      </div>
    </section>
  `;
}

function renderAssessmentDimensionsPage() {
  const { builder, dimensions, currentDimension, questions } = ensureAssessmentEditorState();
  const phoneStage = document.querySelector('.phone-stage');
  phoneStage.innerHTML = `
    <div class="template-page">
      <div class="template-page-head">
        <div>
          <h2>设置维度</h2>
          <p>维度是一级对象。每个维度下面管理题目；每道题下面管理全部选项。</p>
        </div>
        <button type="button" class="btn primary" id="v2-add-dimension">添加维度</button>
      </div>
      <div class="template-grid-3">
        <section class="template-panel">
          <div class="template-panel-head"><div><h3>维度列表</h3><p>点击只切换当前编辑对象，不新增数据。</p></div></div>
          <div class="template-panel-body"><div class="dimension-list">${renderDimensionCards(dimensions)}</div></div>
        </section>
        <section class="template-panel">
          <div class="template-panel-head">
            <div><h3>${escapeHtml(currentDimension?.name || '未选择维度')}：题目与选项</h3><p>所有选项都会进入 H5 预览和提交计算。</p></div>
            <button type="button" class="btn secondary" id="v2-add-question" ${currentDimension ? '' : 'disabled'}>添加题目</button>
          </div>
          <div class="template-panel-body">
            ${currentDimension
              ? (questions.length ? questions.map((question, index) => renderQuestionEditor(question, index, currentDimension)).join('') : '<div class="empty-state">当前维度还没有题目。</div>')
              : '<div class="empty-state">请先添加或选择一个维度。</div>'}
          </div>
        </section>
      </div>
    </div>
  `;
  phoneStage.querySelector('#v2-add-dimension').addEventListener('click', () => addAssessmentDimensionV2());
  phoneStage.querySelectorAll('[data-dimension-key]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedDimensionKey = button.dataset.dimensionKey;
      renderWorkspace();
    });
  });
  phoneStage.querySelector('#v2-add-question')?.addEventListener('click', () => addAssessmentQuestionV2(currentDimension));
  bindDimensionQuestionEvents(phoneStage, currentDimension);
  setInspectorContent(
    '设置维度',
    '当前维度的名称、计分和展示规则统一在这里维护。',
    `${renderDimensionSettingsForm(currentDimension)}
    <section class="config-group">
      <div class="helper-note">删除维度会二次确认。第一版采用“删除维度时连同该维度下题目和选项一起删除”的策略。</div>
      <button type="button" class="btn primary" data-next-step="results" style="width:100%;margin-top:12px;">下一步：结果配置</button>
    </section>`,
  );
  bindDimensionSettingEvents(inspectorBodyEl, builder, currentDimension);
  inspectorBodyEl.querySelector('[data-next-step]').addEventListener('click', () => setAssessmentTemplateStep('results'));
}

function bindDimensionQuestionEvents(root, dimension) {
  root.querySelectorAll('.question-card-v2').forEach((card) => {
    card.addEventListener('click', (event) => {
      if (event.target.closest('input,select,button,textarea')) return;
      state.selectedQuestionKey = card.dataset.questionKey;
      renderWorkspace();
    });
  });
  root.querySelectorAll('[data-question-field]').forEach((input) => {
    input.addEventListener('input', (event) => updateQuestionFieldV2(input, event));
    input.addEventListener('change', (event) => updateQuestionFieldV2(input, event));
  });
  root.querySelectorAll('[data-option-field]').forEach((input) => {
    input.addEventListener('input', (event) => updateOptionFieldV2(input, event));
    input.addEventListener('change', (event) => updateOptionFieldV2(input, event));
  });
  root.querySelectorAll('[data-add-option]').forEach((button) => {
    button.addEventListener('click', () => addOptionV2(button.dataset.addOption));
  });
  root.querySelectorAll('[data-remove-option]').forEach((button) => {
    button.addEventListener('click', () => removeOptionV2(button.closest('[data-question-key]')?.dataset.questionKey, button.dataset.removeOption));
  });
  root.querySelectorAll('[data-remove-question]').forEach((button) => {
    button.addEventListener('click', () => removeQuestionV2(button.dataset.removeQuestion));
  });
  root.querySelectorAll('[data-move-question]').forEach((button) => {
    button.addEventListener('click', () => moveQuestionV2(button.dataset.moveQuestion, Number(button.dataset.direction || 0)));
  });
}

function bindDimensionSettingEvents(root, builder, dimension) {
  if (!dimension) return;
  root.querySelector('#v2-dim-name')?.addEventListener('input', (event) => {
    dimension.name = event.target.value;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-summary')?.addEventListener('input', (event) => {
    dimension.summary = event.target.value;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-weight')?.addEventListener('input', (event) => {
    dimension.weight = event.target.value;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-scoring')?.addEventListener('change', (event) => {
    dimension.scoring_method = event.target.value;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-category-method')?.addEventListener('change', (event) => {
    dimension.category_method = event.target.value;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-enabled')?.addEventListener('change', (event) => {
    dimension.enabled = event.target.checked;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-total')?.addEventListener('change', (event) => {
    dimension.participates_in_total_score = event.target.checked;
    updateDraftIndicator();
  });
  root.querySelector('#v2-dim-show')?.addEventListener('change', (event) => {
    dimension.show_in_result = event.target.checked;
    updateDraftIndicator();
  });
  root.querySelector('#v2-delete-dimension')?.addEventListener('click', () => deleteAssessmentDimensionV2(dimension.key));
  root.querySelector('#v2-move-dimension-up')?.addEventListener('click', () => moveAssessmentDimensionV2(dimension.key, -1));
  root.querySelector('#v2-move-dimension-down')?.addEventListener('click', () => moveAssessmentDimensionV2(dimension.key, 1));
}

function addAssessmentDimensionV2() {
  const builder = ensureAssessmentBuilder();
  const name = `维度 ${builder.dimensions.length + 1}`;
  const key = uniqueAssessmentKey(name, builder.dimensions.map((dimension) => dimension.key));
  const dimension = createAssessmentDimension({ key, name }, builder.dimensions.length);
  builder.dimensions.push(dimension);
  state.selectedDimensionKey = dimension.key;
  state.selectedAssessmentTypeKey = dimension.types[0]?.key || '';
  renderWorkspace();
  showToast('已添加维度');
}

function deleteAssessmentDimensionV2(dimensionKey) {
  const builder = ensureAssessmentBuilder();
  const dimension = builder.dimensions.find((item) => item.key === dimensionKey);
  if (!dimension) return;
  const count = questionsForDimension(dimensionKey).length;
  if (!window.confirm(`删除维度“${dimension.name}”会同时删除该维度下 ${count} 道题和全部选项，确认删除吗？`)) return;
  builder.dimensions = builder.dimensions.filter((item) => item.key !== dimensionKey);
  state.questionnaire.questions = state.questionnaire.questions
    .filter((question) => question.assessment_dimension_key !== dimensionKey)
    .map((question, index) => ({ ...question, sort_order: index + 1 }));
  state.selectedDimensionKey = builder.dimensions[0]?.key || '';
  renderWorkspace();
  showToast('维度已删除');
}

function moveAssessmentDimensionV2(dimensionKey, direction) {
  const builder = ensureAssessmentBuilder();
  const index = builder.dimensions.findIndex((item) => item.key === dimensionKey);
  const nextIndex = index + direction;
  if (index < 0 || nextIndex < 0 || nextIndex >= builder.dimensions.length) return;
  const items = [...builder.dimensions];
  const [moved] = items.splice(index, 1);
  items.splice(nextIndex, 0, moved);
  builder.dimensions = items.map((item, itemIndex) => ({ ...item, sort_order: itemIndex + 1 }));
  renderWorkspace();
}

function addAssessmentQuestionV2(dimension) {
  if (!dimension) return;
  const question = createQuestion('single_choice', {
    type: 'single_choice',
    title: '新测评题',
    required: true,
    assessment_dimension_key: dimension.key,
    options: (dimension.types || []).slice(0, 4).map((type, index) => ({
      option_text: `选项 ${index + 1}`,
      score: index + 1,
      assessment_type_key: type.key,
      tag_codes: [],
    })),
  }, state.questionnaire.questions.length);
  state.questionnaire.questions.push(question);
  state.selectedQuestionKey = question.local_key;
  renderWorkspace();
}

function findQuestionByLocalKey(questionKey) {
  return state.questionnaire.questions.find((question) => question.local_key === questionKey) || null;
}

function updateQuestionFieldV2(input, event) {
  const question = input.closest('[data-question-key]') ? findQuestionByLocalKey(input.closest('[data-question-key]').dataset.questionKey) : null;
  if (!question) return;
  const field = input.dataset.questionField;
  if (field === 'required') question.required = event.target.checked;
  else if (field === 'type') {
    question.type = event.target.value;
    if (['textarea', 'mobile'].includes(question.type)) question.options = [];
    else if (!question.options.length) question.options = [createOption({}, 0)];
  } else if (field === 'sidebar_profile_field') {
    question.sidebar_profile_field = normalizeSidebarProfileField(event.target.value);
  } else {
    question[field] = event.target.value;
  }
  updateDraftIndicator();
  if (field === 'type') renderWorkspace();
}

function updateOptionFieldV2(input, event) {
  const question = input.closest('[data-question-key]') ? findQuestionByLocalKey(input.closest('[data-question-key]').dataset.questionKey) : null;
  const option = question?.options?.find((item) => item.local_key === input.closest('[data-option-key]')?.dataset.optionKey);
  if (!option) return;
  option[input.dataset.optionField] = input.dataset.optionField === 'score' ? Number(event.target.value || 0) : event.target.value;
  updateDraftIndicator();
}

function addOptionV2(questionKey) {
  const question = findQuestionByLocalKey(questionKey);
  if (!question || ['textarea', 'mobile'].includes(question.type)) return;
  const dimension = getAssessmentDimensionByKey(question.assessment_dimension_key);
  const type = dimension?.types?.[question.options.length % Math.max(1, dimension.types.length)];
  question.options.push(createOption({
    option_text: `选项 ${question.options.length + 1}`,
    score: question.options.length + 1,
    assessment_type_key: type?.key || '',
  }, question.options.length));
  renderWorkspace();
}

function removeOptionV2(questionKey, optionKey) {
  const question = findQuestionByLocalKey(questionKey);
  if (!question) return;
  if (!window.confirm('确认删除这个选项吗？')) return;
  question.options = question.options.filter((option) => option.local_key !== optionKey);
  renderWorkspace();
}

function removeQuestionV2(questionKey) {
  const question = findQuestionByLocalKey(questionKey);
  if (!question) return;
  if (!window.confirm(`确认删除题目“${question.title || '未命名题目'}”吗？该题下的选项也会删除。`)) return;
  state.questionnaire.questions = state.questionnaire.questions.filter((item) => item.local_key !== questionKey);
  renderWorkspace();
}

function moveQuestionV2(questionKey, direction) {
  const question = findQuestionByLocalKey(questionKey);
  if (!question) return;
  const dimensionQuestions = questionsForDimension(question.assessment_dimension_key);
  const from = dimensionQuestions.findIndex((item) => item.local_key === questionKey);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= dimensionQuestions.length) return;
  const ordered = [...dimensionQuestions];
  const [moved] = ordered.splice(from, 1);
  ordered.splice(to, 0, moved);
  const orderedKeys = ordered.map((item) => item.local_key);
  state.questionnaire.questions = state.questionnaire.questions
    .slice()
    .sort((left, right) => {
      const leftIdx = orderedKeys.indexOf(left.local_key);
      const rightIdx = orderedKeys.indexOf(right.local_key);
      if (leftIdx >= 0 && rightIdx >= 0) return leftIdx - rightIdx;
      if (leftIdx >= 0) return -1;
      if (rightIdx >= 0) return 1;
      return Number(left.sort_order || 0) - Number(right.sort_order || 0);
    })
    .map((item, index) => ({ ...item, sort_order: index + 1 }));
  renderWorkspace();
}

function renderAssessmentResultsPage() {
  const { builder, dimensions, currentDimension } = ensureAssessmentEditorState();
  const phoneStage = document.querySelector('.phone-stage');
  const tab = state.assessmentResultTab || 'dimension';
  phoneStage.innerHTML = `
    <div class="template-page">
      <div class="template-page-head">
        <div>
          <h2>结果配置</h2>
          <p>独立配置维度分类结果、总分分层和尾部集中推荐。</p>
        </div>
      </div>
      <div class="result-tabs-v2">
        ${[
          ['dimension', '维度结果配置'],
          ['overall', '总分分层配置'],
          ['final', '尾部集中推荐'],
        ].map(([key, label]) => `<button type="button" class="btn ghost${tab === key ? ' active' : ''}" data-result-tab="${key}">${label}</button>`).join('')}
      </div>
      ${tab === 'dimension' ? renderDimensionResultConfig(dimensions, currentDimension) : ''}
      ${tab === 'overall' ? renderOverallResultConfig(builder) : ''}
      ${tab === 'final' ? renderFinalRecommendationConfig(builder) : ''}
    </div>
  `;
  phoneStage.querySelectorAll('[data-result-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      state.assessmentResultTab = button.dataset.resultTab;
      renderWorkspace();
    });
  });
  bindResultPageEvents(phoneStage);
  setInspectorContent(
    '结果配置',
    '空配置也能进入编辑，课程链接第一版就是普通 URL。',
    `${renderResultInspectorContent(tab, builder)}
    <section class="config-group">
      <div class="helper-note">总分分层是主结果；维度分类结果用于辅助诊断和分课程推荐；尾部集中推荐是统一转化入口。</div>
      <button type="button" class="btn primary" data-next-step="preview" style="width:100%;margin-top:12px;">下一步：H5 预览 / 发布</button>
    </section>`,
  );
  bindResultPageEvents(inspectorBodyEl);
  inspectorBodyEl.querySelector('[data-next-step]').addEventListener('click', () => setAssessmentTemplateStep('preview'));
}

function renderDimensionResultConfig(dimensions, currentDimension) {
  return `
    <div class="template-grid-3">
      <section class="template-panel">
        <div class="template-panel-head"><div><h3>维度列表</h3><p>选择要配置结果的维度。</p></div></div>
        <div class="template-panel-body"><div class="dimension-list">${renderDimensionCards(dimensions)}</div></div>
      </section>
      <section class="template-panel">
        <div class="template-panel-head">
          <div><h3>${escapeHtml(currentDimension?.name || '未选择维度')}：分类结果</h3><p>每个“维度 + 分类”都可以独立配置结果。</p></div>
          <button type="button" class="btn secondary" id="v2-add-type" ${currentDimension ? '' : 'disabled'}>添加维度分类结果</button>
        </div>
        <div class="template-panel-body">
          <div class="result-list-v2">
            ${(currentDimension?.types || []).map((type) => `
              <button type="button" class="result-card-v2${type.key === state.selectedAssessmentTypeKey ? ' active' : ''}" data-type-key="${escapeHtml(type.key)}">
                <div class="dimension-card-title"><span>${escapeHtml(type.name)}</span><span class="template-status ${type.diagnosis || type.summary ? 'ok' : 'warn'}">${type.diagnosis || type.summary ? '已配置' : '待补充'}</span></div>
                <div class="template-muted">${escapeHtml(type.diagnosis || type.summary || '点击进入配置')}</div>
              </button>
            `).join('') || '<div class="empty-state">当前维度还没有分类结果。</div>'}
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderTypeResultForm(type) {
  return `
    <label class="field">分类名称<input data-type-result-field="name" value="${escapeHtml(type.name)}"></label>
    <label class="field">结果标题<input data-type-result-field="title" value="${escapeHtml(type.title || type.name)}"></label>
    <label class="field">寒暄话 / 开场话<textarea data-type-result-field="greeting">${escapeHtml(type.greeting || '')}</textarea></label>
    <label class="field">诊断说明<textarea data-type-result-field="diagnosis">${escapeHtml(type.diagnosis || type.summary || '')}</textarea></label>
    <label class="field">问题提醒<textarea data-type-result-field="problem_hint">${escapeHtml(type.problem_hint || '')}</textarea></label>
    <label class="field">推荐动作<textarea data-type-result-field="recommended_action">${escapeHtml(type.recommended_action || '')}</textarea></label>
    <label class="field">推荐课程名称<input data-type-result-field="course_name" value="${escapeHtml(type.course_name || '')}"></label>
    <label class="field">推荐课程跳转链接<input data-type-result-field="course_url" value="${escapeHtml(type.course_url || '')}" placeholder="https://example.com/course"></label>
    <label class="field">CTA 按钮文案<input data-type-result-field="cta_text" value="${escapeHtml(type.cta_text || '')}"></label>
    <div class="config-head inline">
      <h3>企微打标签</h3>
      <p>命中此维度分类后自动打上所选标签。</p>
    </div>
    <div id="v2-type-tag-host"></div>
    <label class="field"><input data-type-result-field="enabled" type="checkbox" ${type.enabled !== false ? 'checked' : ''}> 启用</label>
    <label class="field"><input data-type-result-field="show_in_result" type="checkbox" ${type.show_in_result !== false ? 'checked' : ''}> 展示在最终结果页</label>
    <button type="button" class="link-btn danger" id="v2-remove-type">删除分类结果</button>
  `;
}

function renderOverallResultConfig(builder) {
  return `
    <div class="template-grid-single">
      <section class="template-panel">
        <div class="template-panel-head">
          <div><h3>总分分层</h3><p>按 min_score <= total_score <= max_score 命中。</p></div>
          <button type="button" class="btn secondary" id="v2-add-overall-level">添加分层</button>
        </div>
        <div class="template-panel-body">
          <div class="result-list-v2">
            ${(builder.overall_levels || []).map((level) => `
              <button type="button" class="tier-card-v2${level.local_key === state.selectedOverallLevelKey ? ' active' : ''}" data-overall-key="${escapeHtml(level.local_key)}">
                <div class="dimension-card-title"><span>${escapeHtml(level.title || '未命名分层')}</span><span>${escapeHtml(String(level.min_score ?? ''))} - ${escapeHtml(String(level.max_score ?? ''))}</span></div>
                <div class="template-muted">${escapeHtml(level.summary || '点击进入配置')}</div>
              </button>
            `).join('') || '<div class="empty-state">还没有总分分层。</div>'}
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderOverallLevelForm(level) {
  return `
    <div class="field-grid compact">
      <label class="field">最低分<input data-overall-field="min_score" type="number" step="1" value="${escapeHtml(String(level.min_score ?? ''))}"></label>
      <label class="field">最高分<input data-overall-field="max_score" type="number" step="1" value="${escapeHtml(String(level.max_score ?? ''))}"></label>
    </div>
    <label class="field">分层名称<input data-overall-field="title" value="${escapeHtml(level.title || '')}"></label>
    <label class="field">寒暄话 / 开场话<textarea data-overall-field="greeting">${escapeHtml(level.greeting || '')}</textarea></label>
    <label class="field">总体诊断<textarea data-overall-field="summary">${escapeHtml(level.summary || '')}</textarea></label>
    <label class="field">推荐动作<textarea data-overall-field="recommended_action">${escapeHtml(level.recommended_action || '')}</textarea></label>
    <label class="field">推荐课程名称<input data-overall-field="course_name" value="${escapeHtml(level.course_name || '')}"></label>
    <label class="field">推荐课程跳转链接<input data-overall-field="course_url" value="${escapeHtml(level.course_url || '')}" placeholder="https://example.com/course-main"></label>
    <label class="field">CTA 按钮文案<input data-overall-field="cta_text" value="${escapeHtml(level.cta_text || '')}"></label>
    <div class="config-head inline">
      <h3>企微打标签</h3>
      <p>命中此总分分层后自动打上所选标签。</p>
    </div>
    <div id="v2-overall-tag-host"></div>
    <label class="field"><input data-overall-field="enabled" type="checkbox" ${level.enabled !== false ? 'checked' : ''}> 启用</label>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button type="button" class="btn ghost" id="v2-move-overall-up">上移</button>
      <button type="button" class="btn ghost" id="v2-move-overall-down">下移</button>
      <button type="button" class="link-btn danger" id="v2-remove-overall-level">删除分层</button>
    </div>
  `;
}

function renderFinalRecommendationConfig(builder) {
  const finalRec = builder.final_recommendation || {};
  return `
    <div class="template-grid-single">
    <section class="template-panel">
      <div class="template-panel-head"><div><h3>尾部集中推荐</h3><p>展示在最终 H5 结果页底部，不影响总分和维度推荐。</p></div></div>
      <div class="template-panel-body">
        <div class="result-card-v2 active" style="cursor:default;">
          <div class="dimension-card-title">
            <span>${escapeHtml(finalRec.title || '未设置推荐区标题')}</span>
            <span class="template-status ${finalRec.enabled ? 'ok' : 'warn'}">${finalRec.enabled ? '启用' : '未启用'}</span>
          </div>
          <div class="template-muted">${escapeHtml(finalRec.description || '右侧配置推荐区说明、课程链接和 CTA 文案。')}</div>
        </div>
      </div>
    </section>
    </div>
  `;
}

function renderFinalRecommendationForm(builder) {
  const finalRec = builder.final_recommendation || {};
  return `
    <section class="config-group">
      <label class="field"><input data-final-field="enabled" type="checkbox" ${finalRec.enabled ? 'checked' : ''}> 启用尾部集中推荐</label>
      <label class="field">推荐区标题<input data-final-field="title" value="${escapeHtml(finalRec.title || '')}"></label>
      <label class="field">推荐区说明<textarea data-final-field="description">${escapeHtml(finalRec.description || '')}</textarea></label>
      <label class="field">推荐课程名称<input data-final-field="course_name" value="${escapeHtml(finalRec.course_name || '')}"></label>
      <label class="field">推荐课程链接<input data-final-field="course_url" value="${escapeHtml(finalRec.course_url || '')}" placeholder="https://example.com/main-course"></label>
      <label class="field">CTA 按钮文案<input data-final-field="cta_text" value="${escapeHtml(finalRec.cta_text || '')}"></label>
    </section>
  `;
}

function renderResultInspectorContent(tab, builder) {
  if (tab === 'overall') {
    const selectedLevel = selectedOverallLevel();
    return `<section class="config-group">
      <div class="config-head"><h3>分层配置</h3><p>区间重叠会阻止保存；区间缺口会提醒。</p></div>
      ${selectedLevel ? renderOverallLevelForm(selectedLevel) : '<div class="empty-state">选择或添加一个分层。</div>'}
    </section>`;
  }
  if (tab === 'final') {
    return `${renderFinalRecommendationForm(builder)}`;
  }
  const selectedType = selectedAssessmentType();
  return `<section class="config-group">
    <div class="config-head"><h3>分类结果配置</h3><p>课程链接可为空，前端会自动隐藏按钮。</p></div>
    ${selectedType ? renderTypeResultForm(selectedType) : '<div class="empty-state">选择或添加一个分类结果。</div>'}
  </section>`;
}

function bindResultPageEvents(root) {
  root.querySelectorAll('[data-dimension-key]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedDimensionKey = button.dataset.dimensionKey;
      state.selectedAssessmentTypeKey = '';
      renderWorkspace();
    });
  });
  root.querySelector('#v2-add-type')?.addEventListener('click', () => {
    const dimension = selectedAssessmentDimension();
    if (!dimension) return;
    const key = uniqueAssessmentKey(`type_${(dimension.types || []).length + 1}`, (dimension.types || []).map((type) => type.key));
    const type = createAssessmentType({ key, name: `分类 ${dimension.types.length + 1}` }, dimension.types.length);
    dimension.types.push(type);
    state.selectedAssessmentTypeKey = type.key;
    renderWorkspace();
  });
  root.querySelectorAll('[data-type-key]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedAssessmentTypeKey = button.dataset.typeKey;
      renderWorkspace();
    });
  });
  root.querySelectorAll('[data-type-result-field]').forEach((input) => {
    input.addEventListener('input', (event) => updateTypeResultField(input, event));
    input.addEventListener('change', (event) => updateTypeResultField(input, event));
  });
  const typeTagHost = root.querySelector('#v2-type-tag-host');
  if (typeTagHost) {
    const dimension = selectedAssessmentDimension();
    const type = selectedAssessmentType();
    if (dimension && type) {
      const apply = (tagIds) => {
        type.tag_codes = normalizeTagIds(tagIds);
        updateDraftIndicator();
      };
      apply.currentValue = () => type.tag_codes;
      mountTagPicker(typeTagHost, type.tag_codes, apply, {
        type: 'assessment_type',
        dimensionKey: dimension.key,
        typeKey: type.key,
      });
    }
  }
  root.querySelector('#v2-remove-type')?.addEventListener('click', () => {
    const dimension = selectedAssessmentDimension();
    if (!dimension || !state.selectedAssessmentTypeKey) return;
    if (!window.confirm('确认删除这个维度分类结果吗？相关选项上的分类会被清空。')) return;
    state.questionnaire.questions.forEach((question) => {
      if (question.assessment_dimension_key !== dimension.key) return;
      (question.options || []).forEach((option) => {
        if (option.assessment_type_key === state.selectedAssessmentTypeKey) option.assessment_type_key = '';
      });
    });
    dimension.types = dimension.types.filter((type) => type.key !== state.selectedAssessmentTypeKey);
    state.selectedAssessmentTypeKey = dimension.types[0]?.key || '';
    renderWorkspace();
  });
  root.querySelector('#v2-add-overall-level')?.addEventListener('click', () => {
    const builder = ensureAssessmentBuilder();
    const level = createAssessmentLevel({ title: `分层 ${builder.overall_levels.length + 1}` }, builder.overall_levels.length);
    builder.overall_levels.push(level);
    state.selectedOverallLevelKey = level.local_key;
    renderWorkspace();
  });
  root.querySelectorAll('[data-overall-key]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedOverallLevelKey = button.dataset.overallKey;
      renderWorkspace();
    });
  });
  root.querySelectorAll('[data-overall-field]').forEach((input) => {
    input.addEventListener('input', (event) => updateOverallField(input, event));
    input.addEventListener('change', (event) => updateOverallField(input, event));
  });
  const overallTagHost = root.querySelector('#v2-overall-tag-host');
  if (overallTagHost) {
    const level = selectedOverallLevel();
    if (level) {
      const apply = (tagIds) => {
        level.tag_codes = normalizeTagIds(tagIds);
        updateDraftIndicator();
      };
      apply.currentValue = () => level.tag_codes;
      mountTagPicker(overallTagHost, level.tag_codes, apply, {
        type: 'assessment_overall_level',
        levelKey: level.local_key,
      });
    }
  }
  root.querySelector('#v2-remove-overall-level')?.addEventListener('click', () => {
    const builder = ensureAssessmentBuilder();
    if (!state.selectedOverallLevelKey) return;
    if (!window.confirm('确认删除这个总分分层吗？')) return;
    builder.overall_levels = builder.overall_levels.filter((level) => level.local_key !== state.selectedOverallLevelKey);
    state.selectedOverallLevelKey = builder.overall_levels[0]?.local_key || '';
    renderWorkspace();
  });
  root.querySelector('#v2-move-overall-up')?.addEventListener('click', () => moveOverallLevelV2(-1));
  root.querySelector('#v2-move-overall-down')?.addEventListener('click', () => moveOverallLevelV2(1));
  root.querySelectorAll('[data-final-field]').forEach((input) => {
    input.addEventListener('input', (event) => updateFinalRecommendationField(input, event));
    input.addEventListener('change', (event) => updateFinalRecommendationField(input, event));
  });
}

function updateTypeResultField(input, event) {
  const type = selectedAssessmentType();
  if (!type) return;
  const field = input.dataset.typeResultField;
  type[field] = input.type === 'checkbox' ? event.target.checked : event.target.value;
  if (field === 'diagnosis') type.summary = event.target.value;
  updateDraftIndicator();
}

function updateOverallField(input, event) {
  const level = selectedOverallLevel();
  if (!level) return;
  const field = input.dataset.overallField;
  level[field] = input.type === 'checkbox' ? event.target.checked : event.target.value;
  updateDraftIndicator();
}

function updateFinalRecommendationField(input, event) {
  const builder = ensureAssessmentBuilder();
  builder.final_recommendation = builder.final_recommendation || {};
  const field = input.dataset.finalField;
  builder.final_recommendation[field] = input.type === 'checkbox' ? event.target.checked : event.target.value;
  updateDraftIndicator();
}

function moveOverallLevelV2(direction) {
  const builder = ensureAssessmentBuilder();
  const index = builder.overall_levels.findIndex((level) => level.local_key === state.selectedOverallLevelKey);
  const next = index + direction;
  if (index < 0 || next < 0 || next >= builder.overall_levels.length) return;
  const items = [...builder.overall_levels];
  const [moved] = items.splice(index, 1);
  items.splice(next, 0, moved);
  builder.overall_levels = items.map((level, levelIndex) => ({ ...level, sort_order: levelIndex + 1 }));
  renderWorkspace();
}

function renderAssessmentPreviewPage() {
  const { builder, dimensions, currentDimension } = ensureAssessmentEditorState();
  const phoneStage = document.querySelector('.phone-stage');
  const previewQuestions = state.assessmentPreviewMode === 'dimension' && currentDimension
    ? questionsForDimension(currentDimension.key)
    : state.questionnaire.questions;
  phoneStage.innerHTML = `
    <div class="template-page">
      <div class="template-page-head">
        <div>
          <h2>H5 预览 / 发布</h2>
          <p>这里只做最终预览。手机内部滚动，所有题目的所有选项都会完整展示。</p>
        </div>
        <button type="button" class="btn primary" id="v2-publish-save">保存并发布</button>
      </div>
      <div class="template-grid-single">
        <section class="template-panel">
          <div class="template-panel-body">
            <div class="phone-preview-v2"><div class="phone-screen-v2">
              <div class="phone-notch-v2"></div>
              ${state.assessmentPreviewMode === 'result' ? renderH5ResultPreview(builder, dimensions) : renderH5QuestionPreview(previewQuestions)}
            </div></div>
          </div>
        </section>
      </div>
    </div>
  `;
  phoneStage.querySelector('#v2-publish-save').addEventListener('click', () => saveQuestionnaire().catch((error) => showToast(error.message || '保存失败，请检查当前配置后重试', true)));
  setInspectorContent(
    'H5 预览 / 发布',
    '预览控制集中在这里，预览页不承担题目和结果配置主职责。',
    `<section class="config-group">
      <div class="config-head"><h3>预览控制</h3><p>模拟不同问卷范围和结果页命中。</p></div>
      <label class="field">预览范围
        <select id="v2-preview-mode">
          <option value="full" ${state.assessmentPreviewMode === 'full' ? 'selected' : ''}>预览完整问卷</option>
          <option value="dimension" ${state.assessmentPreviewMode === 'dimension' ? 'selected' : ''}>只预览当前维度</option>
          <option value="result" ${state.assessmentPreviewMode === 'result' ? 'selected' : ''}>预览结果页</option>
        </select>
      </label>
      <label class="field">当前维度
        <select id="v2-preview-dimension">
          ${dimensions.map((dimension) => `<option value="${escapeHtml(dimension.key)}" ${dimension.key === state.selectedDimensionKey ? 'selected' : ''}>${escapeHtml(dimension.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field">总分分层
        <select id="v2-preview-overall">
          ${(builder.overall_levels || []).map((level) => `<option value="${escapeHtml(level.local_key)}" ${level.local_key === state.selectedOverallLevelKey ? 'selected' : ''}>${escapeHtml(level.title || '未命名分层')}</option>`).join('')}
        </select>
      </label>
      <button type="button" class="btn ghost" id="v2-save-draft" style="width:100%;margin-top:8px;">保存草稿</button>
    </section>
    <section class="config-group"><div class="helper-note">题目选项会在手机预览里完整显示；高度不够时手机内部滚动。</div></section>`,
  );
  inspectorBodyEl.querySelector('#v2-preview-mode').addEventListener('change', (event) => {
    state.assessmentPreviewMode = event.target.value;
    renderWorkspace();
  });
  inspectorBodyEl.querySelector('#v2-preview-dimension').addEventListener('change', (event) => {
    state.selectedDimensionKey = event.target.value;
    renderWorkspace();
  });
  inspectorBodyEl.querySelector('#v2-preview-overall').addEventListener('change', (event) => {
    state.selectedOverallLevelKey = event.target.value;
    renderWorkspace();
  });
  inspectorBodyEl.querySelector('#v2-save-draft').addEventListener('click', () => saveQuestionnaire().catch((error) => showToast(error.message || '保存失败，请检查当前配置后重试', true)));
}

function renderH5QuestionPreview(questions) {
  return `
    <div class="h5-card-v2">
      <span class="question-type">测评模板预览</span>
      <h3 style="margin:8px 0 6px;">${escapeHtml(state.questionnaire.title || '问卷标题')}</h3>
      <p class="template-muted">${escapeHtml(state.questionnaire.description || '问卷简介')}</p>
    </div>
    ${questions.map((question, index) => `
      <div class="h5-question-v2">
        <div class="question-meta-row"><span>第 ${index + 1} 题</span><span>${escapeHtml(formatQuestionType(question.type))}</span></div>
        <strong>${escapeHtml(question.title || '题目标题')}</strong>
        ${['textarea', 'mobile'].includes(question.type)
          ? `<div class="h5-option-v2"><span>${escapeHtml(question.placeholder_text || '开放输入')}</span></div>`
          : (question.options || []).map((option) => `<div class="h5-option-v2"><span>${escapeHtml(option.option_text || '选项')}</span><small>${escapeHtml(formatAssessmentTypeName(question.assessment_dimension_key, option.assessment_type_key) || option.assessment_type_key || '未分类')} / ${escapeHtml(String(option.score ?? 0))}分</small></div>`).join('')}
      </div>
    `).join('') || '<div class="empty-state">暂无题目</div>'}
  `;
}

function renderH5ResultPreview(builder, dimensions) {
  const level = selectedOverallLevel() || {};
  const finalRec = builder.final_recommendation || {};
  return `
    <div class="h5-card-v2">
      <span class="question-type">测评结果</span>
      <h3 style="margin:8px 0 6px;">${escapeHtml(level.greeting || level.title || '总分分层结果')}</h3>
      <p class="template-muted">${escapeHtml(level.summary || '这里展示总体诊断说明。')}</p>
      ${level.course_url ? `<a class="btn primary" style="margin-top:10px;width:100%;" href="${escapeHtml(level.course_url)}">${escapeHtml(level.cta_text || '查看推荐课程')}</a>` : ''}
    </div>
    ${dimensions.filter((dimension) => dimension.show_in_result !== false).map((dimension) => {
      const type = (dimension.types || [])[0] || {};
      return `<div class="h5-result-card-v2">
        <strong>${escapeHtml(dimension.name)}：${escapeHtml(type.title || type.name || '未分类')}</strong>
        <p class="template-muted">${escapeHtml(type.diagnosis || type.summary || '暂未配置该分类结果。')}</p>
        ${type.recommended_action ? `<p class="template-muted">${escapeHtml(type.recommended_action)}</p>` : ''}
        ${type.course_url ? `<a class="btn ghost" style="margin-top:8px;width:100%;" href="${escapeHtml(type.course_url)}">${escapeHtml(type.cta_text || '查看课程')}</a>` : ''}
      </div>`;
    }).join('')}
    ${finalRec.enabled ? `<div class="h5-card-v2">
      <strong>${escapeHtml(finalRec.title || '下一步建议')}</strong>
      <p class="template-muted">${escapeHtml(finalRec.description || '')}</p>
      ${finalRec.course_url ? `<a class="btn primary" style="margin-top:10px;width:100%;" href="${escapeHtml(finalRec.course_url)}">${escapeHtml(finalRec.cta_text || '查看完整课程')}</a>` : ''}
    </div>` : ''}
  `;
}

function renderAssessmentTemplateWorkspaceV2() {
  ensureAssessmentEditorState();
  renderTopbar();
  renderAssessmentStepNav();
  if (state.assessmentStep === 'basic') renderAssessmentBasicPage();
  else if (state.assessmentStep === 'dimensions') renderAssessmentDimensionsPage();
  else if (state.assessmentStep === 'results') renderAssessmentResultsPage();
  else renderAssessmentPreviewPage();
  renderList();
  updateDraftIndicator();
}

function renderInspector() {
  if (editorConfig.defaultAssessment) {
    if (state.assessmentStep === 'basic') renderAssessmentBasicPage();
    else if (state.assessmentStep === 'dimensions') renderAssessmentDimensionsPage();
    else if (state.assessmentStep === 'results') renderAssessmentResultsPage();
    else renderAssessmentPreviewPage();
    return;
  }
  if (state.ruleMode) {
    const rule = currentRule();
    renderRuleInspector(rule);
    return;
  }
  if (state.selection.kind === 'questionnaire') {
    renderQuestionnaireInspector();
    return;
  }
  if (state.selection.kind === 'assessment') {
    renderAssessmentInspector();
    return;
  }
  if (state.selection.kind === 'assessment_template_group') {
    const group = assessmentTemplateGroupById(state.selection.templateId);
    if (group) {
      renderAssessmentTemplateGroupInspector(group);
      return;
    }
    state.selection = { kind: 'questionnaire' };
    renderQuestionnaireInspector();
    return;
  }
  if (state.selection.kind === 'question') {
    const question = currentQuestion();
    if (!question) {
      state.selection = { kind: 'questionnaire' };
      renderInspector();
      return;
    }
    renderQuestionInspector(question);
    return;
  }
  renderQuestionnaireInspector();
}

function renderWorkspace() {
  if (editorConfig.defaultAssessment) {
    renderAssessmentTemplateWorkspaceV2();
    return;
  }
  renderTopbar();
  renderPreview();
  renderInspector();
  renderList();
}

function addQuestion(type) {
  const dimension = editorConfig.defaultAssessment ? assessmentDimensions()[0] : null;
  const questionSeed = editorConfig.defaultAssessment && type === 'single_choice' && dimension
    ? {
        type: 'single_choice',
        title: '新测评题',
        required: true,
        assessment_dimension_key: dimension.key,
        assessment_template_id: DEFAULT_ASSESSMENT_TEMPLATE_ID,
        assessment_template_name: DEFAULT_ASSESSMENT_TEMPLATE_NAME,
        options: (dimension.types || []).slice(0, 4).map((assessmentType, index) => ({
          option_text: `选项 ${index + 1}`,
          score: index + 1,
          assessment_type_key: assessmentType.key,
          tag_codes: [],
          sort_order: index + 1,
        })),
      }
    : {};
  const question = createQuestion(type, questionSeed, state.questionnaire.questions.length);
  state.questionnaire.questions.push(question);
  state.ruleMode = false;
  state.selection = { kind: 'question', key: question.local_key };
  renderWorkspace();
}

function addRule() {
  const rule = createRule({}, state.questionnaire.score_rules.length);
  state.questionnaire.score_rules.push(rule);
  state.ruleMode = true;
  state.lastRuleKey = rule.local_key;
  state.selection = { kind: 'rule', key: rule.local_key };
  renderWorkspace();
}

function openAssessmentSettings() {
  if (!editorConfig.defaultAssessment) {
    const existingGroup = assessmentTemplateGroups()[0] || null;
    if (existingGroup) {
      selectAssessmentTemplateGroup(existingGroup.id);
      return;
    }
    state.selection = { kind: 'assessment' };
    renderWorkspace();
    return;
  }
  selectAssessmentSettings();
}

function applyAssessmentPreset() {
  const preset = buildSiyuanIpAssessmentPreset();
  if (editorConfig.defaultAssessment) {
    state.questionnaire.assessment_enabled = true;
    state.questionnaire.assessment_config = preset.assessment_config;
    state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(preset.assessment_config);
    state.questionnaire.name = preset.name;
    state.questionnaire.title = preset.title;
    state.questionnaire.description = preset.description;
    state.questionnaire.questions = preset.questions
      .map((question, index) => createQuestionFromAssessmentPreset(question, index))
      .map((question, index) => ({ ...question, sort_order: index + 1 }));
    state.ruleMode = false;
    state.selection = { kind: 'question', key: state.questionnaire.questions[0]?.local_key || '' };
    renderWorkspace();
    showToast(`已填入“${DEFAULT_ASSESSMENT_TEMPLATE_NAME}”示例模板`);
    return;
  }
  const existingGroup = assessmentTemplateGroupById(DEFAULT_ASSESSMENT_TEMPLATE_ID);
  if (existingGroup) {
    selectAssessmentTemplateGroup(DEFAULT_ASSESSMENT_TEMPLATE_ID);
    showToast('当前问卷已经添加过这个测评模板');
    return;
  }
  state.questionnaire.assessment_enabled = true;
  state.questionnaire.assessment_config = preset.assessment_config;
  state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(preset.assessment_config);
  const existingTitles = new Set(state.questionnaire.questions.map((question) => String(question.title || '').trim()));
  const questionsToInsert = preset.questions
    .filter((question) => !existingTitles.has(String(question.title || '').trim()))
    .map((question, index) => createQuestionFromAssessmentPreset(
      question,
      state.questionnaire.questions.length + index,
    ));
  state.questionnaire.questions = [
    ...state.questionnaire.questions,
    ...questionsToInsert,
  ].map((question, index) => ({ ...question, sort_order: index + 1 }));
  if (questionsToInsert.length) {
    state.ruleMode = false;
    state.selection = { kind: 'assessment_template_group', templateId: DEFAULT_ASSESSMENT_TEMPLATE_ID };
  } else {
    state.selection = { kind: 'assessment_template_group', templateId: DEFAULT_ASSESSMENT_TEMPLATE_ID };
  }
  renderWorkspace();
  showToast(questionsToInsert.length ? `已添加“${DEFAULT_ASSESSMENT_TEMPLATE_NAME}”整组模板` : '模板题目已经在当前问卷里');
}

async function applyAssessmentTemplateFromQuestionnaire(questionnaireId) {
  const existingGroup = assessmentTemplateGroups()[0] || null;
  if (existingGroup) {
    selectAssessmentTemplateGroup(existingGroup.id);
    showToast('当前问卷已经添加过测评模板，如需更换请先删除整组模板');
    return;
  }
  const id = Number(questionnaireId);
  if (!id) {
    throw new Error('请选择一个已保存的测评模板');
  }
  const data = await fetchJson(`/api/admin/questionnaires/${id}`);
  const template = data.questionnaire || {};
  if (!isSavedAssessmentTemplateAsset(template)) {
    throw new Error('这个问卷不是通过「创建测评问卷模板」保存的模板资产，不能作为模板添加');
  }
  const templateQuestions = (template.questions || []).filter((question) => question.assessment_dimension_key);
  if (!templateQuestions.length) {
    throw new Error('这个测评模板还没有配置带维度的测评题');
  }
  const templateReference = {
    id: assessmentTemplateReferenceId(template),
    name: assessmentTemplateReferenceName(template),
  };
  const sourceConfig = normalizeAssessmentConfig(template.assessment_config);
  const assessmentConfig = {
    ...sourceConfig,
    template_id: templateReference.id,
    template_name: templateReference.name,
    asset_kind: 'assessment_template_reference',
    source_questionnaire_id: id,
  };
  state.questionnaire.assessment_enabled = true;
  state.questionnaire.assessment_config = assessmentConfig;
  state.questionnaire.assessment_builder = createAssessmentBuilderFromConfig(assessmentConfig);
  const startIndex = state.questionnaire.questions.length;
  const clonedQuestions = templateQuestions.map((question, index) => createQuestionFromAssessmentTemplateQuestion(
    question,
    startIndex + index,
    templateReference,
  ));
  state.questionnaire.questions = [
    ...state.questionnaire.questions,
    ...clonedQuestions,
  ].map((question, index) => ({ ...question, sort_order: index + 1 }));
  state.ruleMode = false;
  state.selection = { kind: 'assessment_template_group', templateId: templateReference.id };
  renderWorkspace();
  showToast(`已添加“${templateReference.name}”整组测评模板`);
}

function validateUrlField(value, label) {
  const text = String(value || '').trim();
  if (!text) return;
  try {
    const parsed = new URL(text);
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      throw new Error(`${label}必须以 http:// 或 https:// 开头`);
    }
  } catch (error) {
    if (error && /必须/.test(error.message || '')) throw error;
    throw new Error(`${label}格式不正确，请填写完整链接或留空`);
  }
}

function validateAssessmentConfigBeforeSave() {
  if (!state.questionnaire?.assessment_enabled) return;
  const builder = ensureAssessmentBuilder();
  (builder.overall_levels || []).forEach((level, index) => {
    const min = level.min_score === '' || level.min_score === null ? null : Number(level.min_score);
    const max = level.max_score === '' || level.max_score === null ? null : Number(level.max_score);
    if (min === null || Number.isNaN(min)) {
      throw new Error(`总分分层“${level.title || `分层 ${index + 1}`}”的最低分必须填写数字`);
    }
    if (max === null || Number.isNaN(max)) {
      throw new Error(`总分分层“${level.title || `分层 ${index + 1}`}”的最高分必须填写数字`);
    }
    if (max < min) {
      throw new Error(`总分分层“${level.title || `分层 ${index + 1}`}”的最高分必须大于等于最低分`);
    }
    validateUrlField(level.course_url, `总分分层“${level.title || `分层 ${index + 1}`}”的课程链接`);
  });
  const enabledLevels = (builder.overall_levels || [])
    .filter((level) => level.enabled !== false)
    .map((level, index) => ({
      index,
      title: level.title || `分层 ${index + 1}`,
      min: Number(level.min_score),
      max: Number(level.max_score),
    }))
    .sort((left, right) => left.min - right.min);
  for (let index = 1; index < enabledLevels.length; index += 1) {
    const previous = enabledLevels[index - 1];
    const current = enabledLevels[index];
    if (current.min <= previous.max) {
      throw new Error(`总分分层区间重叠：“${previous.title}”和“${current.title}”分数范围有交叉，请调整后保存`);
    }
  }
  const hasGap = enabledLevels.some((level, index) => index > 0 && level.min > enabledLevels[index - 1].max + 1);
  if (hasGap) {
    showToast('总分分层存在分数缺口。可以保存，但未命中的分数会在 H5 结果页使用兜底结果。');
  }
  (builder.dimensions || []).forEach((dimension) => {
    (dimension.types || []).forEach((type) => {
      validateUrlField(type.course_url, `维度“${dimension.name}”分类“${type.name}”的课程链接`);
    });
  });
  validateUrlField(builder.final_recommendation?.course_url, '尾部集中推荐课程链接');
}

function validateOtherOptionsBeforeSave() {
  (state.questionnaire?.questions || []).forEach((question, questionIndex) => {
    if (['textarea', 'mobile'].includes(question.type)) return;
    const title = question.title || `题目 ${questionIndex + 1}`;
    const otherOptions = (question.options || []).filter((option) => Boolean(option.is_other));
    if (otherOptions.length > 1) {
      throw new Error(`题目“${title}”最多只能设置一个其它选项`);
    }
    otherOptions.forEach((option) => {
      const maxLength = normalizeOtherMaxLength(option.other_max_length);
      if (!Number.isFinite(maxLength) || maxLength < 1 || maxLength > 200) {
        throw new Error(`题目“${title}”的其它选项最多输入字数必须在 1 到 200 之间`);
      }
    });
  });
}

async function saveQuestionnaire() {
  validateAssessmentConfigBeforeSave();
  validateOtherOptionsBeforeSave();
  const wasEditing = Boolean(state.currentId);
  const payload = serializePayload();
  const url = state.currentId ? `/api/admin/questionnaires/${state.currentId}` : '/api/admin/questionnaires';
  const method = state.currentId ? 'PUT' : 'POST';
  const data = await fetchJson(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  resetDraft(data.questionnaire);
  state.editorMode = state.currentId ? 'edit' : 'new';
  if (!wasEditing && state.currentId) {
    window.history.replaceState({}, '', `/admin/questionnaires/${state.currentId}`);
  }
  await loadList();
  showToast(wasEditing ? '问卷已更新' : '问卷已创建');
}

if (backLinkEl) {
  backLinkEl.addEventListener('click', (event) => {
    if (confirmDiscardChanges()) return;
    event.preventDefault();
  });
}
document.getElementById('reset-btn').addEventListener('click', () => {
  if (editorConfig.defaultAssessment) {
    saveQuestionnaire().catch((error) => showToast(error.message || '保存失败，请检查当前配置后重试', true));
    return;
  }
  if (!confirmDiscardChanges()) return;
  if (state.currentId) {
    loadQuestionnaire(state.currentId, { skipConfirm: true }).catch((error) => showToast(error.message || '重置失败，请稍后重试', true));
    return;
  }
  resetDraft();
});
document.getElementById('save-btn').addEventListener('click', () => saveQuestionnaire().catch((error) => showToast(error.message || '保存失败，请检查当前配置后重试', true)));
if (document.getElementById('reload-list-btn')) {
  document.getElementById('reload-list-btn').addEventListener('click', () => loadList());
}
if (listSearchEl) {
  listSearchEl.addEventListener('input', (event) => {
    state.listSearch = event.target.value || '';
    renderList();
  });
}
if (statusFilterEl) {
  statusFilterEl.addEventListener('change', (event) => {
    state.statusFilter = event.target.value || 'all';
    renderList();
  });
}
document.getElementById('add-single')?.addEventListener('click', () => addQuestion('single_choice'));
document.getElementById('add-multi')?.addEventListener('click', () => addQuestion('multi_choice'));
document.getElementById('add-textarea')?.addEventListener('click', () => addQuestion('textarea'));
document.getElementById('add-mobile')?.addEventListener('click', () => {
  if (editorConfig.defaultAssessment) {
    applyAssessmentPreset();
    return;
  }
  addQuestion('mobile');
});
document.getElementById('open-assessment-settings')?.addEventListener('click', () => openAssessmentSettings());
document.getElementById('add-rule')?.addEventListener('click', () => {
  if (editorConfig.defaultAssessment) {
    selectAssessmentSettings();
    return;
  }
  enterRuleMode();
});
document.getElementById('drawer-close').addEventListener('click', closeDrawer);
drawerOverlayEl.addEventListener('click', (event) => {
  if (event.target === drawerOverlayEl) closeDrawer();
});

if (editorConfig.initialQuestionnaire) {
  resetDraft(editorConfig.initialQuestionnaire);
} else {
  resetDraft();
}
const bootTasks = [loadAvailableTags(), loadList()];
Promise.all(bootTasks).then(() => {
  renderWorkspace();
}).catch((error) => {
  showToast(error.message || '页面初始化失败，请刷新后重试', true);
  renderWorkspace();
});
