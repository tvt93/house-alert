"""irres.be serves two kinds of detail page. Pages with a slug carry
og:title/og:image; pages reached as /pand/<id>/<id> carry neither, head the
listing with the town alone and put the title in a paragraph above the
price. Both kinds state the CMS status and the address only in the
BreadcrumbList: "FOR_SALE_ORDER_ENDED - 9051 Sint-Denijs-Westrem - Pieter
Pauwel Rubenslaan 1". The sitemap lists ~770 ended mandates next to the
45 houses for sale, all still priced."""

from house_alert.extract import breadcrumb_status, detect_status, extract_listing

PLACEHOLDER = "data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%2F%3E"


def irres_page(status: str, street: str = "Pieter Pauwel Rubenslaan 1") -> str:
    return f"""<html><head><title>Irres</title>
<meta content="Irres" property="og:site_name">
<meta content="https://irres.be/pand/8851631/8851631" property="og:url">
</head><body>
<header class="site-header"><nav class="nav-container">
  <picture class="menu-image"><img src="{PLACEHOLDER}" alt="Other house"
    srcset="/uploads_c/siteassets/94091/other-house.jpg 360w"></picture>
</nav></header>
<main>
<div class="custom-cell | trigger-fullscreen">
  <picture><img src="{PLACEHOLDER}" alt="File Store reference"
    srcset="/uploads_c/siteassets/8851631-9051-sint-denijs-westrem/10149882/photo-1.jpg 360w, /uploads_c/siteassets/8851631-9051-sint-denijs-westrem/10149882/photo-1-big.jpg 1200w"></picture>
</div>
<div>
  <h2 class="text-45px">Sint-Denijs-Westrem</h2>
  <p class="text-2xl">Charmante te renoveren gezinswoning op uiterst bereikbare ligging in Sint-Denijs-Westrem</p>
  <p class="border-t-2">€ 495.000</p>
</div>
<h2>Kenmerken</h2>
<ul>
  <li data-value="Terrein oppervlakte"><p>589 m²</p></li>
  <li data-value="Bewoonbare oppervlakte"><p>196 m²</p></li>
  <li data-value="Slaapkamers"><p>4</p></li>
  <li data-value="Beschikbaarheid"><p>Bij akte</p></li>
</ul>
</main>
<footer class="footer"><p>Irres, Kortrijksesteenweg 19, 9830 Sint-Martens-Latem</p></footer>
<script type="application/ld+json">{{"@context":"http://schema.org","@graph":[
 {{"@type":"RealEstateListing","url":"https://irres.be/pand/8851631/8851631"}},
 {{"@id":"#identity","@type":"RealEstateAgent","address":{{"@type":"PostalAddress",
   "addressLocality":"Sint-Martens-Latem","postalCode":"9830","streetAddress":"Kortrijksesteenweg 19"}}}},
 {{"@type":"BreadcrumbList","itemListElement":[
   {{"@type":"ListItem","item":"https://irres.be/","name":"Home","position":1}},
   {{"@type":"ListItem","item":"https://irres.be/pand/8851631/8851631",
     "name":"{status} - 9051 Sint-Denijs-Westrem - {street}","position":2}}]}}]}}</script>
</body></html>"""


def test_ended_mandate_is_not_a_listing():
    # The page still prints the price and the facts; only the breadcrumb
    # says the sale order ended. 42 of 117 stored Irres rows were such pages.
    page = irres_page("FOR_SALE_ORDER_ENDED")
    assert breadcrumb_status(page) == "sold"
    assert detect_status(page, "https://irres.be/pand/8851631/8851631") == "sold"
    assert extract_listing("https://irres.be/pand/8851631/8851631", page, source="Irres") is None


def test_option_in_breadcrumb():
    page = irres_page("OPTION_FOR_SALE")
    assert detect_status(page, "https://irres.be/pand/8851631/8851631") == "option"


def test_slugless_page_reads_title_address_and_photo():
    page = irres_page("FOR_SALE")
    assert detect_status(page, "https://irres.be/pand/8851631/8851631") == "available"
    listing = extract_listing("https://irres.be/pand/8851631/8851631", page, source="Irres")
    assert listing is not None
    # The title is the sentence above the price, not the site name.
    assert listing.title == ("Charmante te renoveren gezinswoning op uiterst bereikbare "
                             "ligging in Sint-Denijs-Westrem")
    assert listing.property_type == "house"
    assert (listing.postal_code, listing.city) == ("9051", "Sint-Denijs-Westrem")
    assert listing.street == "Pieter Pauwel Rubenslaan 1"       # not the office's door
    assert listing.price == 495000
    assert listing.bedrooms == 4
    assert listing.living_area_m2 == 196
    assert listing.plot_area_m2 == 589
    # The photo sits in a srcset behind a transparent placeholder; the nav
    # strip's other house, also lazy, is chrome.
    assert listing.image_url == ("https://irres.be/uploads_c/siteassets/"
                                 "8851631-9051-sint-denijs-westrem/10149882/photo-1.jpg")


def test_breadcrumb_unit_suffix_is_dropped_from_the_street():
    page = irres_page("FOR_SALE", street="Nederpolder 16 - 0201")
    listing = extract_listing("https://irres.be/pand/9097767/9097767", page, source="Irres")
    assert listing is not None
    assert listing.street == "Nederpolder 16"


def test_untitled_page_is_named_by_its_breadcrumb_address():
    # irres.be/pand/9099452: the agency typed no title; the page shows the
    # town, the price and the facts. The address beats "Irres" in a mail.
    page = irres_page("FOR_SALE", street="Casselrylaan 35").replace(
        '<p class="text-2xl">Charmante te renoveren gezinswoning op uiterst bereikbare '
        'ligging in Sint-Denijs-Westrem</p>', '')
    listing = extract_listing("https://irres.be/pand/9099452/9099452", page, source="Irres")
    assert listing is not None
    assert listing.title == "Casselrylaan 35, 9051 Sint-Denijs-Westrem"
    assert listing.street == "Casselrylaan 35"


def test_plain_breadcrumbs_say_nothing():
    page = """<html><body><h1>Woning te koop in Gent</h1><p>€ 300.000</p><p>3 slaapkamers</p>
    <script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[
      {"@type":"ListItem","name":"Home","position":1},
      {"@type":"ListItem","name":"Woning te koop in Gent","position":2}]}</script>
    </body></html>"""
    assert breadcrumb_status(page) == ""
    assert detect_status(page, "https://x.be/te-koop/1") == "available"
