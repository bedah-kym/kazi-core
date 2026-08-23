from django.test.runner import DiscoverRunner

DEFAULT_TEST_LABELS = (
    'orchestration',
    'workflows',
    'chatbot',
    'travel',
    'notifications',
    'users',
    'payments',
    'Api',
    'tests',
)


class KaziDiscoverRunner(DiscoverRunner):
    default_test_labels = DEFAULT_TEST_LABELS

    def run_tests(self, test_labels=None, extra_tests=None, **kwargs):
        return super().run_tests(
            list(test_labels) if test_labels else list(self.default_test_labels),
            extra_tests=extra_tests,
            **kwargs,
        )
