# Course marketing playbook (organic only)

Nora reads this through `course_catalog` when she picks a product, a channel
and a lesson. It reaches her prompt as quoted data (`wrap_untrusted`), not as
instructions. The words of every post are still composed in code
(`compose.py`); this file only helps her choose.

## Sources

Read on 2026-09-29 and treated as untrusted input. Only tactics were taken
from them. Nothing below quotes them.

- Shopify, "How to Sell on Social Media With Shopify" (published 2024-07-19):
  https://www.shopify.com/blog/sell-products-social-media
- Shopify, "Social Media Marketing Courses for Small Business" (published
  2023-01-23): https://www.shopify.com/blog/social-media-marketing-courses

The second article is a list of courses that *teach* social media marketing.
It is not about marketing a course, so it contributed little.

## What the sources say that applies here

1. Write captions that inform, and add a few relevant hashtags. Every feed
   post already ends with its product's fixed hashtags.
2. Answer the comments and questions under a post. ATLAS cannot do this: no
   comment-reply action exists. Miro answers them himself.
3. Each platform has a different audience, so pick the product whose audience
   matches the platform (see "Product by platform" below).
4. Use free material and email sign-up as a way in. Here that is the Free
   Family Scam-Safety Kit, which is the email lead magnet. Link to its product
   page and call it free. No other price claim is made for it.
5. Send people from the post to the store. Every post links to its product
   page with UTM tags. Instagram and TikTok take no links in posts, so they
   depend on a link in the profile bio. ATLAS cannot set a bio. The course
   reels lane (`content/course.py`) covers those two platforms.

## What does not apply, and why

- **Shoppable posts, product tags, Instagram and Facebook Shops, Facebook
  Marketplace, TikTok Shop, YouTube product shelves and Pinterest's Verified
  Merchant badge.** Each needs a Shopify sales-channel app or a Commerce
  account that ATLAS does not have. Each would also be a store or account
  setting, which Miro would have to change himself.
- **Live shopping events and creator collaborations.** These need a person on
  camera or a deal with another person. Neither is an ATLAS action.
- **Anything paid: ads, boosts, promoted posts or paid creators.** No such
  action exists in this module, and money out is Tier 3.

## ATLAS's own rules for this module (not from the sources)

- **Post shape.** Each post has a hook, a value line and a call to action.
  The hook is a lesson's own first sentence. The value line is its safety line.
  The call to action is the price read live, or "free" for the kit, plus the
  tagged link. On X and Bluesky, and on any 500-character feed where the full
  post is too long, the hook goes out with the title, the price and the link.
- **Blog.** Each product gets one SEO article for the Shopify blog, handed to
  Miro. The title is at most 60 characters and the meta description at most
  155. The body is 700 to 1,200 words of the product's own lesson excerpts,
  copied word for word. The product page is linked in the opening paragraph
  and in the call to action.
- **Mix.** Rotation gives every product its fair share of the weekly cap
  (`COURSE_POSTS_PER_WEEK`, default 7). Within a product's turn:
  - Operator: X, LinkedIn and Threads (founders and builders), plus its
    Spanish, Portuguese and Arabic variants.
  - Everyday AI for Adults: LinkedIn and Facebook (parents and small-business
    owners), plus X and Threads.
  - Safe and Simple AI 60+: Facebook first (adults 60+ and their adult
    children), then LinkedIn and Threads. English only.
  - Bundle: Facebook and LinkedIn, framed for a household with more than one
    generation learning. English only.
  - Free kit: any feed it is allowed on. It is the lead magnet, so it is the
    first thing to try on a new channel. English only.
- **Tone for the 60+ guide and the kit.** Stay calm and practical, and never
  try to frighten anyone. Any number must be copied word for word from the
  lesson files, with its source.

## Product by platform (allowed channels are enforced in `catalog.py`)

| Product | Platforms |
|---|---|
| operator | x, bluesky, threads, mastodon, linkedin, facebook, telegram, shopify_blog |
| adults | x, bluesky, threads, mastodon, linkedin, facebook, telegram, shopify_blog |
| sixty_plus | facebook, linkedin, threads, shopify_blog |
| bundle | facebook, linkedin, threads, shopify_blog |
| scam_kit | facebook, linkedin, threads, x, bluesky, mastodon, shopify_blog |
