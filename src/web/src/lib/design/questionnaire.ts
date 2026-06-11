import { generateObject, type LanguageModel } from "ai";
import { z } from "zod";
import type { Format } from "./format";

/**
 * Build a tailored questions.html for any design brief.
 *
 * Architecture: LLM proposes the QUESTION STRUCTURE (via structured
 * output — generateObject with a Zod schema), the server renders the
 * HTML from that JSON. This means:
 *   - Model can't bail mid-output or wrap in markdown — schema enforces format.
 *   - Questions adapt to ANY brief, not just hardcoded topics. A dental
 *     clinic gets dental-clinic questions, a record label gets record
 *     label questions, a metaverse community gets metaverse questions.
 *   - Render is template-driven so the form HTML is always correct
 *     (the postMessage contract, chip selection, custom-text fallback).
 *
 * If the LLM call fails, we fall back to a generic per-format question
 * set so the user is never blocked.
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

type Question = {
  qid: string;
  legend: string;
  options: string[];
};

// Subject-specific question sets. When a brief mentions a known topic
// (restaurant, SaaS, portfolio, etc.) we ask probes that fit THAT topic
// instead of the generic format-default ones. Detection is keyword-based
// — cheap, predictable, no LLM call.
type Topic =
  | "restaurant"
  | "cafe-bar"
  | "saas"
  | "ecommerce"
  | "portfolio"
  | "agency"
  | "blog"
  | "event"
  | "nonprofit"
  | "pitch-deck"
  | "fitness"
  | "real-estate"
  | "music"
  | "education";

function detectTopic(text: string): Topic | null {
  const t = text.toLowerCase();
  if (/\brestaurants?|bistro|eatery|trattoria|brasserie|izakaya|ramen\s+shop\b/.test(t))
    return "restaurant";
  if (/\bcafes?|coffee\s+shop|coffeehouse|wine\s+bar|cocktail\s+bar|pub\b/.test(t))
    return "cafe-bar";
  if (/\bsaas|b2b\s+software|api\s+platform|developer\s+tool|enterprise\s+software\b/.test(t))
    return "saas";
  if (/\b(?:e-?commerce|online\s+store|shopify|product\s+store|fashion\s+brand)\b/.test(t))
    return "ecommerce";
  if (/\bportfolios?|personal\s+site|my\s+work\b/.test(t))
    return "portfolio";
  if (/\bagency|studio|design\s+agency|consultancy\b/.test(t))
    return "agency";
  if (/\bblog|newsletter|publication|magazine\b/.test(t))
    return "blog";
  if (/\bevents?|conference|festival|wedding|launch\s+party\b/.test(t))
    return "event";
  if (/\bnon[- ]?profit|charity|ngo|foundation\b/.test(t))
    return "nonprofit";
  if (/\bpitch\s+deck|investor\s+deck|seed\s+round|series\s+[a-c]\b/.test(t))
    return "pitch-deck";
  if (/\bfitness|gym|yoga\s+studio|crossfit|trainer|workout\b/.test(t))
    return "fitness";
  if (/\breal\s+estate|property|listings?|realtor\b/.test(t))
    return "real-estate";
  if (/\bmusic|band|album|musician|dj|record\s+label\b/.test(t))
    return "music";
  if (/\bcourse|tutoring|school|edtech|university\b/.test(t))
    return "education";
  return null;
}

function topicQuestions(topic: Topic, aestheticChips: string[]): Question[] {
  switch (topic) {
    case "restaurant":
      return [
        { qid: "name", legend: "Restaurant name?", options: [] },
        {
          qid: "cuisine",
          legend: "Cuisine?",
          options: ["Italian", "French", "Japanese", "Mexican", "Mediterranean", "Modern American", "Vegan / plant-based", "Pan-Asian"],
        },
        {
          qid: "vibe",
          legend: "Vibe?",
          options: ["Fine dining", "Bistro / casual", "Date-night romantic", "Family-friendly", "Trendy / hip", "Neighborhood spot"],
        },
        { qid: "location", legend: "City or neighborhood?", options: [] },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (signature dishes, awards, hours, story)", options: [] },
      ];
    case "cafe-bar":
      return [
        { qid: "name", legend: "Place name?", options: [] },
        {
          qid: "kind",
          legend: "What kind of place?",
          options: ["Specialty coffee", "Wine bar", "Cocktail bar", "Cafe + bookshop", "Brunch spot", "Late-night bar"],
        },
        {
          qid: "vibe",
          legend: "Vibe?",
          options: ["Cozy", "Industrial / minimalist", "Vintage / retro", "Modern luxe", "Loud / energetic", "Quiet / focus"],
        },
        { qid: "location", legend: "City or neighborhood?", options: [] },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (specialty drink, food menu, events)", options: [] },
      ];
    case "saas":
      return [
        { qid: "name", legend: "Product name?", options: [] },
        { qid: "what", legend: "What does it do? (one sentence)", options: [] },
        {
          qid: "audience",
          legend: "Who's the buyer?",
          options: ["Developers", "Engineering managers", "PMs", "Sales / RevOps", "Marketing teams", "Founders / CEOs", "Operations", "Designers"],
        },
        {
          qid: "pricing_visible",
          legend: "Show pricing on the page?",
          options: ["Yes — public tiers", "Yes — single price", "No — talk to sales"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (key feature, customers, benchmarks)", options: [] },
      ];
    case "ecommerce":
      return [
        { qid: "name", legend: "Store / brand name?", options: [] },
        {
          qid: "category",
          legend: "What do you sell?",
          options: ["Apparel", "Home goods", "Beauty / skincare", "Food / beverage", "Jewelry", "Tech accessories", "Art / prints", "Books"],
        },
        {
          qid: "audience",
          legend: "Customer profile?",
          options: ["Premium / luxury", "Everyday / mass-market", "Niche enthusiasts", "Gift buyers", "Younger (Gen Z)", "Older (40+)"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (founder story, materials, hero product)", options: [] },
      ];
    case "portfolio":
      return [
        { qid: "name", legend: "Your name?", options: [] },
        {
          qid: "discipline",
          legend: "What's your work?",
          options: ["Designer", "Engineer", "Photographer", "Illustrator", "Writer", "Filmmaker", "Architect", "Multi-disciplinary"],
        },
        {
          qid: "what_to_show",
          legend: "What's on the page?",
          options: ["Selected projects", "Full case studies", "Just imagery", "Resume + projects", "Writing + projects"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything specific? (named clients, awards, focus)", options: [] },
      ];
    case "agency":
      return [
        { qid: "name", legend: "Agency name?", options: [] },
        {
          qid: "discipline",
          legend: "What do you do?",
          options: ["Brand identity", "Web / digital product", "Marketing campaigns", "Architecture / interiors", "Photography / film", "Content / editorial"],
        },
        {
          qid: "audience",
          legend: "Who are clients?",
          options: ["Startups", "Enterprise", "Cultural institutions", "Luxury brands", "Local businesses"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (named clients, manifesto, services list)", options: [] },
      ];
    case "blog":
      return [
        { qid: "name", legend: "Publication name?", options: [] },
        { qid: "topic", legend: "What's it about?", options: [] },
        {
          qid: "voice",
          legend: "Voice?",
          options: ["Personal / first-person", "Reported / journalistic", "Long-form essay", "Tutorial / how-to", "Curated / link-list"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (cadence, archive, author bios)", options: [] },
      ];
    case "event":
      return [
        { qid: "name", legend: "Event name?", options: [] },
        {
          qid: "kind",
          legend: "What kind of event?",
          options: ["Conference", "Festival", "Wedding", "Product launch", "Workshop", "Gala / fundraiser"],
        },
        { qid: "date_location", legend: "When and where?", options: [] },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (speakers, schedule, RSVP)", options: [] },
      ];
    case "nonprofit":
      return [
        { qid: "name", legend: "Organization name?", options: [] },
        { qid: "cause", legend: "What's the cause?", options: [] },
        {
          qid: "primary_action",
          legend: "Primary visitor action?",
          options: ["Donate", "Volunteer", "Learn more", "Subscribe to updates", "Apply for a program"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (impact stats, named programs, founder)", options: [] },
      ];
    case "pitch-deck":
      return [
        { qid: "name", legend: "Company name?", options: [] },
        { qid: "what", legend: "What does the company do? (one sentence)", options: [] },
        {
          qid: "stage",
          legend: "Stage?",
          options: ["Pre-seed", "Seed", "Series A", "Series B+", "Bootstrapped"],
        },
        {
          qid: "key_emphasis",
          legend: "What should the deck emphasize?",
          options: ["Market size", "Traction / growth", "Team", "Product demo", "Vision / why now"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (real metrics, named customers, raise size)", options: [] },
      ];
    case "fitness":
      return [
        { qid: "name", legend: "Studio / brand name?", options: [] },
        {
          qid: "kind",
          legend: "What kind of fitness?",
          options: ["Yoga", "Pilates", "CrossFit / strength", "HIIT / bootcamp", "Spin / cardio", "Personal training", "Multi-class boutique"],
        },
        {
          qid: "audience",
          legend: "Audience?",
          options: ["Beginners", "Athletes / advanced", "Women-focused", "All ages", "Senior-friendly"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (signature class, location, schedule)", options: [] },
      ];
    case "real-estate":
      return [
        { qid: "name", legend: "Listing or agency name?", options: [] },
        {
          qid: "kind",
          legend: "What's listed?",
          options: ["Single luxury property", "Apartment building", "Vacation rental", "Agency portfolio", "Commercial space"],
        },
        { qid: "location", legend: "Location?", options: [] },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (price, sq ft, amenities)", options: [] },
      ];
    case "music":
      return [
        { qid: "name", legend: "Artist or band name?", options: [] },
        {
          qid: "kind",
          legend: "What's on the page?",
          options: ["New album / release", "Tour dates", "Full bio + discography", "Single / song page", "EPK for press"],
        },
        {
          qid: "genre",
          legend: "Genre?",
          options: ["Rock / indie", "Pop", "Electronic / dance", "Hip-hop / R&B", "Jazz / soul", "Classical", "Folk / country", "Experimental"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (release date, cover art, streaming links)", options: [] },
      ];
    case "education":
      return [
        { qid: "name", legend: "Course or school name?", options: [] },
        { qid: "topic", legend: "What's taught?", options: [] },
        {
          qid: "audience",
          legend: "Audience?",
          options: ["Beginners", "Working professionals", "Students", "Parents (kids' learning)", "Specific niche"],
        },
        {
          qid: "format_kind",
          legend: "Format?",
          options: ["Self-paced online", "Live cohort", "1-on-1 tutoring", "In-person", "Hybrid"],
        },
        { qid: "aesthetic", legend: "Visual aesthetic?", options: aestheticChips },
        { qid: "specifics", legend: "Anything else? (curriculum, instructor, price)", options: [] },
      ];
  }
}

function questionsForFormat(format: Format, topic: Topic | null): Question[] {
  const aestheticChips = [
    "Editorial",
    "Minimalist",
    "Brutalist",
    "Cinema",
    "Playful",
    "Futuristic",
    "Handcrafted",
    "Corporate",
  ];
  // Topic-specific questions take priority — they're more useful than
  // generic format-default chips when the brief gives us a clear subject.
  if (topic) return topicQuestions(topic, aestheticChips);
  if (format === "slides") {
    return [
      {
        qid: "subject",
        legend: "What's it about?",
        options: ["Pitch deck", "Product launch", "Quarterly review", "Kickoff", "Workshop"],
      },
      {
        qid: "audience",
        legend: "Who's the audience?",
        options: ["Investors", "Customers", "Internal team", "Conference talk", "Executive board"],
      },
      {
        qid: "aesthetic",
        legend: "What aesthetic?",
        options: aestheticChips,
      },
      {
        qid: "slide_count",
        legend: "How many slides?",
        options: ["5", "8", "10", "15"],
      },
      {
        qid: "specifics",
        legend: "Anything specific to include? (numbers, named people, key claims)",
        options: [],
      },
    ];
  }
  if (format === "prototype") {
    return [
      {
        qid: "subject",
        legend: "What kind of app?",
        options: ["Mobile app", "Web app", "Tablet/kiosk", "Watch face"],
      },
      {
        qid: "audience",
        legend: "Who uses it?",
        options: ["Consumers", "B2B operators", "Internal tool", "Power users", "Kids"],
      },
      {
        qid: "screens",
        legend: "Which screens to include?",
        options: ["Home + list + detail", "Onboarding + main + settings", "Login + dashboard", "Camera + capture + review"],
      },
      {
        qid: "aesthetic",
        legend: "What aesthetic?",
        options: aestheticChips,
      },
      {
        qid: "specifics",
        legend: "Anything specific to include? (real product names, content)",
        options: [],
      },
    ];
  }
  if (format === "landing") {
    return [
      {
        qid: "subject",
        legend: "What's the product or service?",
        options: ["B2B SaaS", "Consumer product", "Restaurant", "Agency / portfolio", "Marketplace", "Local business"],
      },
      {
        qid: "audience",
        legend: "Who's it for?",
        options: ["Consumers", "Small businesses", "Enterprise buyers", "Designers / creatives", "Engineers"],
      },
      {
        qid: "aesthetic",
        legend: "What aesthetic?",
        options: aestheticChips,
      },
      {
        qid: "sections",
        legend: "What sections matter most? (beyond hero + footer)",
        options: ["Pricing", "Features", "Testimonials", "FAQ", "Stats", "Case studies"],
      },
      {
        qid: "specifics",
        legend: "Anything specific to include? (real name, tagline, key claim)",
        options: [],
      },
    ];
  }
  if (format === "onepager") {
    return [
      {
        qid: "subject",
        legend: "What's it about?",
        options: ["Weekly status", "Board update", "Project brief", "Memo", "Performance report"],
      },
      {
        qid: "audience",
        legend: "Who's reading it?",
        options: ["Internal team", "Leadership", "Board", "Client", "Public"],
      },
      {
        qid: "aesthetic",
        legend: "What aesthetic?",
        options: aestheticChips,
      },
      {
        qid: "specifics",
        legend: "What numbers or facts go in it?",
        options: [],
      },
    ];
  }
  // infographic
  return [
    {
      qid: "subject",
      legend: "What's the topic?",
      options: ["Market sizing", "Survey results", "Process / how-it-works", "Comparison", "Annual recap"],
    },
    {
      qid: "audience",
      legend: "Who's it for?",
      options: ["Social share", "Press release", "Investors", "Customers", "Internal"],
    },
    {
      qid: "aesthetic",
      legend: "What aesthetic?",
      options: aestheticChips,
    },
    {
      qid: "specifics",
      legend: "Specific stats, dates, or names to include?",
      options: [],
    },
  ];
}

// Schema constrains the LLM's output to validated form structure. Any
// model that can do structured output works (deepseek-chat included).
// Free-text questions have empty options[]; chip questions have 4-8.
const QuestionSchema = z.object({
  qid: z
    .string()
    .regex(/^[a-z][a-z0-9_]*$/, "lowercase snake_case identifier"),
  legend: z.string().min(3).max(80),
  options: z.array(z.string().min(1).max(40)).max(8),
});
const QuestionnaireSchema = z.object({
  questions: z.array(QuestionSchema).min(3).max(7),
});

/**
 * Generate a brief-tailored questionnaire via structured output.
 * Returns the JSON shape; render to HTML with `renderQuestionsHtml`.
 *
 * Why structured output not raw text: lets the model decide WHAT to ask
 * for any topic the user mentions, but the schema guarantees the
 * response is renderable JSON — never a half-finished string, never
 * markdown-wrapped HTML, never a "let me think about it" bail.
 */
export async function generateQuestions(
  brief: string,
  format: Format,
  model: LanguageModel,
): Promise<Question[]> {
  const formatHint = formatHintFor(format);
  const system = `You are a design intake assistant. Given a brief, produce 4-6 questions to ask the user before designing.

Rules:
- First question MUST ask for the name / subject / brand (free-text, options=[]).
- Middle questions probe specifics RELEVANT to what the brief mentions — if the brief says "restaurant", ask cuisine/vibe/location; if "SaaS", ask audience/pricing/key feature; if "dental clinic", ask specialty/location/insurance — adapt to the brief.
- Include exactly ONE aesthetic/visual-style question with options: ["Editorial","Minimalist","Brutalist","Cinema","Playful","Futuristic","Handcrafted","Corporate"].
- Last question MUST be free-text "Anything else?" (options=[]).
- Mix chip questions (3-8 short options each) with free-text questions (options=[]). 2-3 free-text total is good.
- qid is lowercase snake_case.
- legend is a short question (under 60 chars), no period at end.
- Options are SHORT label nouns/adjectives — never full sentences.

${formatHint}`;

  try {
    const result = await generateObject({
      model,
      schema: QuestionnaireSchema,
      system,
      prompt: `Brief: "${brief.trim()}"`,
    });
    return result.object.questions;
  } catch (err) {
    console.warn(
      "[questionnaire] generateObject failed, falling back to format questions:",
      err,
    );
    // Detect the topic from the brief so the fallback asks topic-tailored
    // questions (restaurant → cuisine/vibe, SaaS → audience/pricing, …)
    // instead of generic ones. detectTopic returns null for an unknown
    // topic, in which case questionsForFormat falls through to the generic
    // set — so this is strictly better than the previous hardcoded null.
    return questionsForFormat(format, detectTopic(brief));
  }
}

function formatHintFor(format: Format): string {
  switch (format) {
    case "slides":
      return "The user wants a slide deck. Probe: audience, slide count, key emphasis.";
    case "prototype":
      return "The user wants an app prototype. Probe: app kind, target screens, audience.";
    case "landing":
      return "The user wants a landing page. Probe: product/service kind, audience, what sections to include.";
    case "onepager":
      return "The user wants a one-page document. Probe: audience, key facts/numbers to include.";
    case "infographic":
      return "The user wants an infographic. Probe: topic specifics, key stats, audience.";
  }
}

/** Render the questions JSON to the questions.html string. */
export function renderQuestionsHtml(brief: string, questions: Question[]): string {
  const headline = escapeHtml(brief.trim() || "your design");

  const fieldsets = questions
    .map((q) => {
      const chips = q.options
        .map(
          (opt) =>
            `<button type="button" data-value="${escapeHtml(opt)}" class="rounded-full border border-gray-300 px-3 py-1.5 text-sm">${escapeHtml(opt)}</button>`,
        )
        .join("\n            ");
      const chipsBlock = q.options.length
        ? `<div class="flex flex-wrap gap-2" data-question="${q.qid}">
            ${chips}
          </div>
          <input type="text" data-other-for="${q.qid}" class="mt-2 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:border-gray-900" placeholder="Or type your own…">`
        : `<div data-question="${q.qid}"></div>
          <textarea data-other-for="${q.qid}" rows="3" class="mt-2 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:border-gray-900" placeholder="Anything specific…"></textarea>`;
      return `        <fieldset>
          <legend class="text-base font-semibold mb-2 block">${escapeHtml(q.legend)}</legend>
          ${chipsBlock}
        </fieldset>`;
    })
    .join("\n\n");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A few questions</title>
  <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', system-ui, sans-serif; }
    h1, h2, legend { font-family: 'Bricolage Grotesque', sans-serif; }
    button[data-value] { transition: all 0.12s ease; }
    button[data-value]:hover { border-color: #1f2937; }
    button[data-value][data-selected] { background: #1f2937; color: #fff; border-color: #1f2937; }
  </style>
</head>
<body class="bg-white text-gray-900 p-8">
  <h1 class="text-3xl font-semibold mb-1">A few questions about &ldquo;${headline}&rdquo;</h1>
  <p class="text-gray-500 mb-8">Pick the closest match for each — type your own if &ldquo;Other&rdquo; fits better.</p>

  <form id="jarvis-questions" class="max-w-2xl space-y-7">

${fieldsets}

    <button type="submit" class="rounded-md bg-gray-900 text-white px-5 py-2 text-sm font-medium hover:bg-black">Continue</button>
  </form>

  <script>
    (function(){
      var form = document.getElementById('jarvis-questions');
      if (!form) return;
      var selected = {};
      var groups = form.querySelectorAll('[data-question]');
      for (var i = 0; i < groups.length; i++) {
        (function(group){
          var qid = group.getAttribute('data-question');
          var chips = group.querySelectorAll('[data-value]');
          for (var j = 0; j < chips.length; j++) {
            chips[j].addEventListener('click', function(e){
              e.preventDefault();
              for (var k = 0; k < chips.length; k++) chips[k].removeAttribute('data-selected');
              this.setAttribute('data-selected', 'true');
              selected[qid] = this.getAttribute('data-value');
            });
          }
        })(groups[i]);
      }
      form.addEventListener('submit', function(e){
        e.preventDefault();
        var answers = {};
        for (var i = 0; i < groups.length; i++) {
          var qid = groups[i].getAttribute('data-question');
          var input = form.querySelector('[data-other-for="' + qid + '"]');
          if (input && input.value.trim()) {
            answers[qid] = input.value.trim();
          } else if (selected[qid]) {
            answers[qid] = selected[qid];
          }
        }
        parent.postMessage({ type: 'jarvis:design:questions:submit', answers: answers }, '*');
      });
    })();
  </script>
</body>
</html>
`;
}
