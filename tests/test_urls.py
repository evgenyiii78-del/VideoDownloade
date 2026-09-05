import unittest

from downloader import (
    UnsupportedUrlError,
    build_instagram_proxy_urls,
    extract_supported_url,
)


class ExtractSupportedUrlTests(unittest.TestCase):
    def test_instagram_reel(self):
        url, platform = extract_supported_url(
            "look https://www.instagram.com/reel/ABC123/?igsh=x"
        )
        self.assertEqual(platform, "Instagram")
        self.assertTrue(url.startswith("https://www.instagram.com/reel/ABC123/"))

    def test_tiktok_video(self):
        url, platform = extract_supported_url(
            "https://www.tiktok.com/@demo/video/123456"
        )
        self.assertEqual(platform, "TikTok")
        self.assertEqual(url, "https://www.tiktok.com/@demo/video/123456")

    def test_tiktok_short_link(self):
        url, platform = extract_supported_url("https://vm.tiktok.com/ABCDEFG/")
        self.assertEqual(platform, "TikTok")
        self.assertEqual(url, "https://vm.tiktok.com/ABCDEFG/")

    def test_unsupported_domain(self):
        with self.assertRaises(UnsupportedUrlError):
            extract_supported_url("https://example.com/video/1")

    def test_fake_suffix_domain_is_rejected(self):
        with self.assertRaises(UnsupportedUrlError):
            extract_supported_url("https://instagram.com.evil.example/reel/123")


class InstagramProxyTests(unittest.TestCase):
    def test_reel_proxy_urls(self):
        urls = build_instagram_proxy_urls(
            "https://www.instagram.com/reel/DchsKq9othJ/?igsh=ABC"
        )
        self.assertEqual(
            urls,
            [
                "https://vxinstagram.com/reel/DchsKq9othJ/",
                "https://ddinstagram.com/reel/DchsKq9othJ/",
            ],
        )

    def test_post_proxy_urls(self):
        urls = build_instagram_proxy_urls(
            "https://instagram.com/p/ABC123/?utm_source=test"
        )
        self.assertEqual(
            urls[0],
            "https://vxinstagram.com/p/ABC123/",
        )

    def test_non_instagram_has_no_proxy_urls(self):
        self.assertEqual(
            build_instagram_proxy_urls("https://www.tiktok.com/@x/video/1"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
