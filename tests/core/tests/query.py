# -*- coding: utf-8 -*-
import datetime
from django.conf import settings
from django.test import TestCase
from haystack import connections, connection_router, reset_search_queries
from haystack.backends import SQ, BaseSearchQuery
from haystack.exceptions import FacetingError
from haystack import indexes
from haystack.models import SearchResult
from haystack.query import SearchQuerySet, EmptySearchQuerySet
from haystack.utils.loading import UnifiedIndex
from core.models import MockModel, AnotherMockModel, CharPKMockModel, AFifthMockModel
from core.tests.indexes import ReadQuerySetTestSearchIndex, GhettoAFifthMockModelSearchIndex, TextReadQuerySetTestSearchIndex
from core.tests.mocks import MockSearchQuery, MockSearchBackend, CharPKMockSearchBackend, MixedMockSearchBackend, ReadQuerySetMockSearchBackend, MOCK_SEARCH_RESULTS
from core.tests.views import BasicMockModelSearchIndex, BasicAnotherMockModelSearchIndex

test_pickling = True

try:
    import pickle
except ImportError:
    test_pickling = False


class SQTestCase(TestCase):
    def test_split_expression(self):
        sq = SQ(foo='bar')
        
        self.assertEqual(sq.split_expression('foo'), ('foo', 'exact'))
        self.assertEqual(sq.split_expression('foo__exact'), ('foo', 'exact'))
        self.assertEqual(sq.split_expression('foo__lt'), ('foo', 'lt'))
        self.assertEqual(sq.split_expression('foo__lte'), ('foo', 'lte'))
        self.assertEqual(sq.split_expression('foo__gt'), ('foo', 'gt'))
        self.assertEqual(sq.split_expression('foo__gte'), ('foo', 'gte'))
        self.assertEqual(sq.split_expression('foo__in'), ('foo', 'in'))
        self.assertEqual(sq.split_expression('foo__startswith'), ('foo', 'startswith'))
        self.assertEqual(sq.split_expression('foo__range'), ('foo', 'range'))
        
        # Unrecognized filter. Fall back to exact.
        self.assertEqual(sq.split_expression('foo__moof'), ('foo', 'exact'))
    
    def test_repr(self):
        self.assertEqual(repr(SQ(foo='bar')), '<SQ: AND foo__exact=bar>')
        self.assertEqual(repr(SQ(foo=1)), '<SQ: AND foo__exact=1>')
        self.assertEqual(repr(SQ(foo=datetime.datetime(2009, 5, 12, 23, 17))), '<SQ: AND foo__exact=2009-05-12 23:17:00>')
    
    def test_simple_nesting(self):
        sq1 = SQ(foo='bar')
        sq2 = SQ(foo='bar')
        bigger_sq = SQ(sq1 & sq2)
        self.assertEqual(repr(bigger_sq), '<SQ: AND (foo__exact=bar AND foo__exact=bar)>')
        
        another_bigger_sq = SQ(sq1 | sq2)
        self.assertEqual(repr(another_bigger_sq), '<SQ: AND (foo__exact=bar OR foo__exact=bar)>')
        
        one_more_bigger_sq = SQ(sq1 & ~sq2)
        self.assertEqual(repr(one_more_bigger_sq), '<SQ: AND (foo__exact=bar AND NOT (foo__exact=bar))>')
        
        mega_sq = SQ(bigger_sq & SQ(another_bigger_sq | ~one_more_bigger_sq))
        self.assertEqual(repr(mega_sq), '<SQ: AND ((foo__exact=bar AND foo__exact=bar) AND ((foo__exact=bar OR foo__exact=bar) OR NOT ((foo__exact=bar AND NOT (foo__exact=bar)))))>')


class BaseSearchQueryTestCase(TestCase):
    fixtures = ['bulk_data.json']
    
    def setUp(self):
        super(BaseSearchQueryTestCase, self).setUp()
        self.bsq = BaseSearchQuery()
    
    def test_get_count(self):
        self.bsq.add_filter(SQ(foo='bar'))
        self.assertRaises(NotImplementedError, self.bsq.get_count)
    
    def test_build_query(self):
        self.bsq.add_filter(SQ(foo='bar'))
        self.assertRaises(NotImplementedError, self.bsq.build_query)
    
    def test_add_filter(self):
        self.assertEqual(len(self.bsq.query_filter), 0)
        
        self.bsq.add_filter(SQ(foo='bar'))
        self.assertEqual(len(self.bsq.query_filter), 1)
        
        self.bsq.add_filter(SQ(foo__lt='10'))
        
        self.bsq.add_filter(~SQ(claris='moof'))
        
        self.bsq.add_filter(SQ(claris='moof'), use_or=True)
        
        self.assertEqual(repr(self.bsq.query_filter), '<SQ: OR ((foo__exact=bar AND foo__lt=10 AND NOT (claris__exact=moof)) OR claris__exact=moof)>')
        
        self.bsq.add_filter(SQ(claris='moof'))

        self.assertEqual(repr(self.bsq.query_filter), '<SQ: AND (((foo__exact=bar AND foo__lt=10 AND NOT (claris__exact=moof)) OR claris__exact=moof) AND claris__exact=moof)>')
    
    def test_add_order_by(self):
        self.assertEqual(len(self.bsq.order_by), 0)
        
        self.bsq.add_order_by('foo')
        self.assertEqual(len(self.bsq.order_by), 1)
    
    def test_clear_order_by(self):
        self.bsq.add_order_by('foo')
        self.assertEqual(len(self.bsq.order_by), 1)
        
        self.bsq.clear_order_by()
        self.assertEqual(len(self.bsq.order_by), 0)
    
    def test_add_model(self):
        self.assertEqual(len(self.bsq.models), 0)
        self.assertRaises(AttributeError, self.bsq.add_model, object)
        self.assertEqual(len(self.bsq.models), 0)
        
        self.bsq.add_model(MockModel)
        self.assertEqual(len(self.bsq.models), 1)
        
        self.bsq.add_model(AnotherMockModel)
        self.assertEqual(len(self.bsq.models), 2)
    
    def test_set_limits(self):
        self.assertEqual(self.bsq.start_offset, 0)
        self.assertEqual(self.bsq.end_offset, None)
        
        self.bsq.set_limits(10, 50)
        self.assertEqual(self.bsq.start_offset, 10)
        self.assertEqual(self.bsq.end_offset, 50)
    
    def test_clear_limits(self):
        self.bsq.set_limits(10, 50)
        self.assertEqual(self.bsq.start_offset, 10)
        self.assertEqual(self.bsq.end_offset, 50)
        
        self.bsq.clear_limits()
        self.assertEqual(self.bsq.start_offset, 0)
        self.assertEqual(self.bsq.end_offset, None)
    
    def test_add_boost(self):
        self.assertEqual(self.bsq.boost, {})
        
        self.bsq.add_boost('foo', 10)
        self.assertEqual(self.bsq.boost, {'foo': 10})
    
    def test_add_highlight(self):
        self.assertEqual(self.bsq.highlight, False)
        
        self.bsq.add_highlight()
        self.assertEqual(self.bsq.highlight, True)
    
    def test_more_like_this(self):
        mock = MockModel()
        mock.id = 1
        msq = MockSearchQuery()
        msq.backend = MockSearchBackend('mlt')
        ui = connections['default'].get_unified_index()
        bmmsi = BasicMockModelSearchIndex()
        ui.build(indexes=[bmmsi])
        bmmsi.update()
        msq.more_like_this(mock)
        
        self.assertEqual(msq.get_count(), 23)
        self.assertEqual(int(msq.get_results()[0].pk), MOCK_SEARCH_RESULTS[0].pk)
    
    def test_add_field_facet(self):
        self.bsq.add_field_facet('foo')
        self.assertEqual(self.bsq.facets, set(['foo']))
        
        self.bsq.add_field_facet('bar')
        self.assertEqual(self.bsq.facets, set(['foo', 'bar']))
    
    def test_add_date_facet(self):
        self.bsq.add_date_facet('foo', start_date=datetime.date(2009, 2, 25), end_date=datetime.date(2009, 3, 25), gap_by='day')
        self.assertEqual(self.bsq.date_facets, {'foo': {'gap_by': 'day', 'start_date': datetime.date(2009, 2, 25), 'end_date': datetime.date(2009, 3, 25), 'gap_amount': 1}})
        
        self.bsq.add_date_facet('bar', start_date=datetime.date(2008, 1, 1), end_date=datetime.date(2009, 12, 1), gap_by='month')
        self.assertEqual(self.bsq.date_facets, {'foo': {'gap_by': 'day', 'start_date': datetime.date(2009, 2, 25), 'end_date': datetime.date(2009, 3, 25), 'gap_amount': 1}, 'bar': {'gap_by': 'month', 'start_date': datetime.date(2008, 1, 1), 'end_date': datetime.date(2009, 12, 1), 'gap_amount': 1}})
    
    def test_add_query_facet(self):
        self.bsq.add_query_facet('foo', 'bar')
        self.assertEqual(self.bsq.query_facets, [('foo', 'bar')])
        
        self.bsq.add_query_facet('moof', 'baz')
        self.assertEqual(self.bsq.query_facets, [('foo', 'bar'), ('moof', 'baz')])
        
        self.bsq.add_query_facet('foo', 'baz')
        self.assertEqual(self.bsq.query_facets, [('foo', 'bar'), ('moof', 'baz'), ('foo', 'baz')])
    
    def test_add_narrow_query(self):
        self.bsq.add_narrow_query('foo:bar')
        self.assertEqual(self.bsq.narrow_queries, set(['foo:bar']))
        
        self.bsq.add_narrow_query('moof:baz')
        self.assertEqual(self.bsq.narrow_queries, set(['foo:bar', 'moof:baz']))
    
    def test_set_result_class(self):
        # Assert that we're defaulting to ``SearchResult``.
        self.assertTrue(issubclass(self.bsq.result_class, SearchResult))
        
        # Custom class.
        class IttyBittyResult(object):
            pass
        
        self.bsq.set_result_class(IttyBittyResult)
        self.assertTrue(issubclass(self.bsq.result_class, IttyBittyResult))
        
        # Reset to default.
        self.bsq.set_result_class(None)
        self.assertTrue(issubclass(self.bsq.result_class, SearchResult))
    
    def test_run(self):
        # Stow.
        self.old_unified_index = connections['default']._index
        self.ui = UnifiedIndex()
        self.bmmsi = BasicMockModelSearchIndex()
        self.bammsi = BasicAnotherMockModelSearchIndex()
        self.ui.build(indexes=[self.bmmsi, self.bammsi])
        connections['default']._index = self.ui
        
        # Update the "index".
        backend = connections['default'].get_backend()
        backend.clear()
        backend.update(self.bmmsi, MockModel.objects.all())
        
        msq = connections['default'].get_query()
        self.assertEqual(len(msq.get_results()), 23)
        self.assertEqual(int(msq.get_results()[0].pk), MOCK_SEARCH_RESULTS[0].pk)
        
        # Restore.
        connections['default']._index = self.old_unified_index
    
    def test_clone(self):
        self.bsq.add_filter(SQ(foo='bar'))
        self.bsq.add_filter(SQ(foo__lt='10'))
        self.bsq.add_filter(~SQ(claris='moof'))
        self.bsq.add_filter(SQ(claris='moof'), use_or=True)
        self.bsq.add_order_by('foo')
        self.bsq.add_model(MockModel)
        self.bsq.add_boost('foo', 2)
        self.bsq.add_highlight()
        self.bsq.add_field_facet('foo')
        self.bsq.add_date_facet('foo', start_date=datetime.date(2009, 1, 1), end_date=datetime.date(2009, 1, 31), gap_by='day')
        self.bsq.add_query_facet('foo', 'bar')
        self.bsq.add_narrow_query('foo:bar')
        
        clone = self.bsq._clone()
        self.assertTrue(isinstance(clone, BaseSearchQuery))
        self.assertEqual(len(clone.query_filter), 2)
        self.assertEqual(len(clone.order_by), 1)
        self.assertEqual(len(clone.models), 1)
        self.assertEqual(len(clone.boost), 1)
        self.assertEqual(clone.highlight, True)
        self.assertEqual(len(clone.facets), 1)
        self.assertEqual(len(clone.date_facets), 1)
        self.assertEqual(len(clone.query_facets), 1)
        self.assertEqual(len(clone.narrow_queries), 1)
        self.assertEqual(clone.start_offset, self.bsq.start_offset)
        self.assertEqual(clone.end_offset, self.bsq.end_offset)
        self.assertEqual(clone.backend.__class__, self.bsq.backend.__class__)
    
    def test_log_query(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        
        # Stow.
        self.old_unified_index = connections['default']._index
        self.ui = UnifiedIndex()
        self.bmmsi = BasicMockModelSearchIndex()
        self.ui.build(indexes=[self.bmmsi])
        connections['default']._index = self.ui
        
        # Update the "index".
        backend = connections['default'].get_backend()
        backend.clear()
        self.bmmsi.update()
        old_debug = settings.DEBUG
        settings.DEBUG = False
        
        msq = connections['default'].get_query()
        self.assertEqual(len(msq.get_results()), 23)
        self.assertEqual(len(connections['default'].queries), 0)
        
        settings.DEBUG = True
        # Redefine it to clear out the cached results.
        msq2 = connections['default'].get_query()
        self.assertEqual(len(msq2.get_results()), 23)
        self.assertEqual(len(connections['default'].queries), 1)
        self.assertEqual(connections['default'].queries[0]['query_string'], '')
        
        msq3 = connections['default'].get_query()
        msq3.add_filter(SQ(foo='bar'))
        len(msq3.get_results())
        self.assertEqual(len(connections['default'].queries), 2)
        self.assertEqual(connections['default'].queries[0]['query_string'], '')
        self.assertEqual(connections['default'].queries[1]['query_string'], '')
        
        # Restore.
        connections['default']._index = self.old_unified_index
        settings.DEBUG = old_debug


class CharPKMockModelSearchIndex(indexes.SearchIndex):
    text = indexes.CharField(document=True, model_attr='key')
    
    def get_model(self):
        return CharPKMockModel


class SearchQuerySetTestCase(TestCase):
    fixtures = ['bulk_data.json']
    
    def setUp(self):
        super(SearchQuerySetTestCase, self).setUp()
        
        # Stow.
        self.old_unified_index = connections['default']._index
        self.ui = UnifiedIndex()
        self.bmmsi = BasicMockModelSearchIndex()
        self.cpkmmsi = CharPKMockModelSearchIndex()
        self.ui.build(indexes=[self.bmmsi, self.cpkmmsi])
        connections['default']._index = self.ui
        
        # Update the "index".
        backend = connections['default'].get_backend()
        backend.clear()
        backend.update(self.bmmsi, MockModel.objects.all())
        
        self.msqs = SearchQuerySet()
        
        # Stow.
        self.old_debug = settings.DEBUG
        settings.DEBUG = True
        
        reset_search_queries()
    
    def tearDown(self):
        # Restore.
        connections['default']._index = self.old_unified_index
        settings.DEBUG = self.old_debug
        super(SearchQuerySetTestCase, self).tearDown()
    
    def test_len(self):
        self.assertEqual(len(self.msqs), 23)
    
    def test_repr(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        self.assertEqual(repr(self.msqs), "[<SearchResult: core.mockmodel (pk=u'1')>, <SearchResult: core.mockmodel (pk=u'2')>, <SearchResult: core.mockmodel (pk=u'3')>, <SearchResult: core.mockmodel (pk=u'4')>, <SearchResult: core.mockmodel (pk=u'5')>, <SearchResult: core.mockmodel (pk=u'6')>, <SearchResult: core.mockmodel (pk=u'7')>, <SearchResult: core.mockmodel (pk=u'8')>, <SearchResult: core.mockmodel (pk=u'9')>, <SearchResult: core.mockmodel (pk=u'10')>, <SearchResult: core.mockmodel (pk=u'11')>, <SearchResult: core.mockmodel (pk=u'12')>, <SearchResult: core.mockmodel (pk=u'13')>, <SearchResult: core.mockmodel (pk=u'14')>, <SearchResult: core.mockmodel (pk=u'15')>, <SearchResult: core.mockmodel (pk=u'16')>, <SearchResult: core.mockmodel (pk=u'17')>, <SearchResult: core.mockmodel (pk=u'18')>, <SearchResult: core.mockmodel (pk=u'19')>, '...(remaining elements truncated)...']")
        self.assertEqual(len(connections['default'].queries), 1)
    
    def test_iter(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        msqs = self.msqs.all()
        results = [int(res.pk) for res in msqs]
        self.assertEqual(results, [res.pk for res in MOCK_SEARCH_RESULTS[:23]])
        self.assertEqual(len(connections['default'].queries), 3)
    
    def test_slice(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        results = self.msqs.all()
        self.assertEqual([int(res.pk) for res in results[1:11]], [res.pk for res in MOCK_SEARCH_RESULTS[1:11]])
        self.assertEqual(len(connections['default'].queries), 1)
        
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        results = self.msqs.all()
        self.assertEqual(int(results[22].pk), MOCK_SEARCH_RESULTS[22].pk)
        self.assertEqual(len(connections['default'].queries), 1)
    
    def test_manual_iter(self):
        results = self.msqs.all()
        
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        
        check = [result.pk for result in results._manual_iter()]
        self.assertEqual(check, [u'1', u'2', u'3', u'4', u'5', u'6', u'7', u'8', u'9', u'10', u'11', u'12', u'13', u'14', u'15', u'16', u'17', u'18', u'19', u'20', u'21', u'22', u'23'])
        
        self.assertEqual(len(connections['default'].queries), 3)
        
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        
        # Test to ensure we properly fill the cache, even if we get fewer
        # results back (not a handled model) than the hit count indicates.
        # This will hang indefinitely if broken.
        old_ui = self.ui
        self.ui.build(indexes=[self.cpkmmsi])
        connections['default']._index = self.ui
        self.cpkmmsi.update()
        
        results = self.msqs.all()
        loaded = [result.pk for result in results._manual_iter()]
        self.assertEqual(loaded, [u'sometext', u'1234'])
        self.assertEqual(len(connections['default'].queries), 1)
        
        connections['default']._index = old_ui
    
    def test_fill_cache(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        results = self.msqs.all()
        self.assertEqual(len(results._result_cache), 0)
        self.assertEqual(len(connections['default'].queries), 0)
        results._fill_cache(0, 10)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 10)
        self.assertEqual(len(connections['default'].queries), 1)
        results._fill_cache(10, 20)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 20)
        self.assertEqual(len(connections['default'].queries), 2)
        
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        
        # Test to ensure we properly fill the cache, even if we get fewer
        # results back (not a handled model) than the hit count indicates.
        sqs = SearchQuerySet().all()
        sqs.query.backend = MixedMockSearchBackend('default')
        results = sqs
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 0)
        self.assertEqual([int(result.pk) for result in results._result_cache if result is not None], [])
        self.assertEqual(len(connections['default'].queries), 0)
        results._fill_cache(0, 10)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 9)
        self.assertEqual([int(result.pk) for result in results._result_cache if result is not None], [1, 2, 3, 4, 5, 6, 7, 8, 10])
        self.assertEqual(len(connections['default'].queries), 2)
        results._fill_cache(10, 20)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 17)
        self.assertEqual([int(result.pk) for result in results._result_cache if result is not None], [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 15, 16, 17, 18, 19, 20])
        self.assertEqual(len(connections['default'].queries), 4)
        results._fill_cache(20, 30)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 20)
        self.assertEqual([int(result.pk) for result in results._result_cache if result is not None], [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 15, 16, 17, 18, 19, 20, 21, 22, 23])
        self.assertEqual(len(connections['default'].queries), 6)
    
    def test_cache_is_full(self):
        reset_search_queries()
        self.assertEqual(len(connections['default'].queries), 0)
        self.assertEqual(self.msqs._cache_is_full(), False)
        results = self.msqs.all()
        fire_the_iterator_and_fill_cache = [result for result in results]
        self.assertEqual(results._cache_is_full(), True)
        self.assertEqual(len(connections['default'].queries), 3)
    
    def test_all(self):
        sqs = self.msqs.all()
        self.assertTrue(isinstance(sqs, SearchQuerySet))
    
    def test_filter(self):
        sqs = self.msqs.filter(content='foo')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 1)
    
    def test_exclude(self):
        sqs = self.msqs.exclude(content='foo')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 1)
    
    def test_order_by(self):
        sqs = self.msqs.order_by('foo')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertTrue('foo' in sqs.query.order_by)
    
    def test_models(self):
        # Stow.
        old_unified_index = connections['default']._index
        ui = UnifiedIndex()
        bmmsi = BasicMockModelSearchIndex()
        bammsi = BasicAnotherMockModelSearchIndex()
        ui.build(indexes=[bmmsi, bammsi])
        connections['default']._index = ui
        
        msqs = SearchQuerySet()
        
        sqs = msqs.all()
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.models), 0)
        
        sqs = msqs.models(MockModel)
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.models), 1)
        
        sqs = msqs.models(MockModel, AnotherMockModel)
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.models), 2)
        
        # This will produce a warning.
        ui.build(indexes=[bmmsi])
        sqs = msqs.models(AnotherMockModel)
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.models), 1)
    
    def test_result_class(self):
        sqs = self.msqs.all()
        self.assertTrue(issubclass(sqs.query.result_class, SearchResult))
        
        # Custom class.
        class IttyBittyResult(object):
            pass
        
        sqs = self.msqs.result_class(IttyBittyResult)
        self.assertTrue(issubclass(sqs.query.result_class, IttyBittyResult))
        
        # Reset to default.
        sqs = self.msqs.result_class(None)
        self.assertTrue(issubclass(sqs.query.result_class, SearchResult))
    
    def test_boost(self):
        sqs = self.msqs.boost('foo', 10)
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.boost.keys()), 1)
    
    def test_highlight(self):
        sqs = self.msqs.highlight()
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(sqs.query.highlight, True)
    
    def test_spelling(self):
        # Test the case where spelling support is disabled.
        sqs = self.msqs.filter(content='Indx')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(sqs.spelling_suggestion(), None)
        self.assertEqual(sqs.spelling_suggestion('indexy'), None)
    
    def test_raw_search(self):
        self.assertEqual(len(self.msqs.raw_search('foo')), 23)
        self.assertEqual(len(self.msqs.raw_search('(content__exact:hello AND content__exact:world)')), 23)
    
    def test_load_all(self):
        # Models with character primary keys.
        sqs = SearchQuerySet()
        sqs.query.backend = CharPKMockSearchBackend('charpk')
        results = sqs.load_all().all()
        self.assertEqual(len(results._result_cache), 0)
        results._fill_cache(0, 2)
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 2)
        
        # If nothing is handled, you get nothing.
        old_ui = connections['default']._index
        ui = UnifiedIndex()
        ui.build(indexes=[])
        connections['default']._index = ui
        
        sqs = self.msqs.load_all()
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs), 0)
        
        connections['default']._index = old_ui
        
        # For full tests, see the solr_backend.
    
    def test_load_all_read_queryset(self):
        # Stow.
        old_ui = connections['default']._index
        ui = UnifiedIndex()
        gafmmsi = GhettoAFifthMockModelSearchIndex()
        ui.build(indexes=[gafmmsi])
        connections['default']._index = ui
        gafmmsi.update()
        
        sqs = SearchQuerySet()
        results = sqs.load_all().all()
        results.query.backend = ReadQuerySetMockSearchBackend('default')
        results._fill_cache(0, 2)
        
        # The deleted result isn't returned
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 1)
        
        # Register a SearchIndex with a read_queryset that returns deleted items
        rqstsi = TextReadQuerySetTestSearchIndex()
        ui.build(indexes=[rqstsi])
        rqstsi.update()
        
        sqs = SearchQuerySet()
        results = sqs.load_all().all()
        results.query.backend = ReadQuerySetMockSearchBackend('default')
        results._fill_cache(0, 2)
        
        # Both the deleted and not deleted items are returned
        self.assertEqual(len([result for result in results._result_cache if result is not None]), 2)
        
        # Restore.
        connections['default']._index = old_ui

    def test_auto_query(self):
        sqs = self.msqs.auto_query('test search -stuff')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), '<SQ: AND (content__exact=test AND content__exact=search AND NOT (content__exact=stuff))>')
        
        sqs = self.msqs.auto_query('test "my thing" search -stuff')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), '<SQ: AND (content__exact=my thing AND content__exact=test AND content__exact=search AND NOT (content__exact=stuff))>')
        
        sqs = self.msqs.auto_query('test "my thing" search \'moar quotes\' -stuff')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), "<SQ: AND (content__exact=my thing AND content__exact=test AND content__exact=search AND content__exact='moar AND content__exact=quotes' AND NOT (content__exact=stuff))>")
        
        sqs = self.msqs.auto_query('test "my thing" search \'moar quotes\' "foo -stuff')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), '<SQ: AND (content__exact=my thing AND content__exact=test AND content__exact=search AND content__exact=\'moar AND content__exact=quotes\' AND content__exact="foo AND NOT (content__exact=stuff))>')
        
        sqs = self.msqs.auto_query('test - stuff')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), '<SQ: AND (content__exact=test AND content__exact=- AND content__exact=stuff)>')
        
        # Ensure bits in exact matches get escaped properly as well.
        sqs = self.msqs.auto_query('"pants:rule"')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(repr(sqs.query.query_filter), '<SQ: AND content__exact=pants:rule>')
    
    def test_count(self):
        self.assertEqual(self.msqs.count(), 23)
    
    def test_facet_counts(self):
        self.assertEqual(self.msqs.facet_counts(), {})
    
    def test_best_match(self):
        self.assertTrue(isinstance(self.msqs.best_match(), SearchResult))
    
    def test_latest(self):
        self.assertTrue(isinstance(self.msqs.latest('pub_date'), SearchResult))
    
    def test_more_like_this(self):
        mock = MockModel()
        mock.id = 1
        
        self.assertEqual(len(self.msqs.more_like_this(mock)), 23)
    
    def test_facets(self):
        sqs = self.msqs.facet('foo')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.facets), 1)
        
        sqs2 = self.msqs.facet('foo').facet('bar')
        self.assertTrue(isinstance(sqs2, SearchQuerySet))
        self.assertEqual(len(sqs2.query.facets), 2)
    
    def test_date_facets(self):
        try:
            sqs = self.msqs.date_facet('foo', start_date=datetime.date(2008, 2, 25), end_date=datetime.date(2009, 2, 25), gap_by='smarblaph')
            self.fail()
        except FacetingError, e:
            self.assertEqual(str(e), "The gap_by ('smarblaph') must be one of the following: year, month, day, hour, minute, second.")
        
        sqs = self.msqs.date_facet('foo', start_date=datetime.date(2008, 2, 25), end_date=datetime.date(2009, 2, 25), gap_by='month')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.date_facets), 1)
        
        sqs2 = self.msqs.date_facet('foo', start_date=datetime.date(2008, 2, 25), end_date=datetime.date(2009, 2, 25), gap_by='month').date_facet('bar', start_date=datetime.date(2007, 2, 25), end_date=datetime.date(2009, 2, 25), gap_by='year')
        self.assertTrue(isinstance(sqs2, SearchQuerySet))
        self.assertEqual(len(sqs2.query.date_facets), 2)
    
    def test_query_facets(self):
        sqs = self.msqs.query_facet('foo', '[bar TO *]')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_facets), 1)
        
        sqs2 = self.msqs.query_facet('foo', '[bar TO *]').query_facet('bar', '[100 TO 499]')
        self.assertTrue(isinstance(sqs2, SearchQuerySet))
        self.assertEqual(len(sqs2.query.query_facets), 2)
        
        # Test multiple query facets on a single field
        sqs3 = self.msqs.query_facet('foo', '[bar TO *]').query_facet('bar', '[100 TO 499]').query_facet('foo', '[1000 TO 1499]')
        self.assertTrue(isinstance(sqs3, SearchQuerySet))
        self.assertEqual(len(sqs3.query.query_facets), 3)
    
    def test_narrow(self):
        sqs = self.msqs.narrow('foo:moof')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.narrow_queries), 1)
    
    def test_clone(self):
        results = self.msqs.filter(foo='bar', foo__lt='10')
        
        clone = results._clone()
        self.assertTrue(isinstance(clone, SearchQuerySet))
        self.assertEqual(str(clone.query), str(results.query))
        self.assertEqual(clone._result_cache, [])
        self.assertEqual(clone._result_count, None)
        self.assertEqual(clone._cache_full, False)
        self.assertEqual(clone._using, results._using)
    
    def test_chaining(self):
        sqs = self.msqs.filter(content='foo')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 1)
        
        # A second instance should inherit none of the changes from above.
        sqs = self.msqs.filter(content='bar')
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 1)
    
    def test_none(self):
        sqs = self.msqs.none()
        self.assertTrue(isinstance(sqs, EmptySearchQuerySet))
        self.assertEqual(len(sqs), 0)
    
    def test___and__(self):
        sqs1 = self.msqs.filter(content='foo')
        sqs2 = self.msqs.filter(content='bar')
        sqs = sqs1 & sqs2
        
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 2)
    
    def test___or__(self):
        sqs1 = self.msqs.filter(content='foo')
        sqs2 = self.msqs.filter(content='bar')
        sqs = sqs1 | sqs2
        
        self.assertTrue(isinstance(sqs, SearchQuerySet))
        self.assertEqual(len(sqs.query.query_filter), 2)


class EmptySearchQuerySetTestCase(TestCase):
    def setUp(self):
        super(EmptySearchQuerySetTestCase, self).setUp()
        self.esqs = EmptySearchQuerySet()
    
    def test_get_count(self):
        self.assertEqual(self.esqs.count(), 0)
        self.assertEqual(len(self.esqs.all()), 0)
    
    def test_filter(self):
        sqs = self.esqs.filter(content='foo')
        self.assertTrue(isinstance(sqs, EmptySearchQuerySet))
        self.assertEqual(len(sqs), 0)
    
    def test_exclude(self):
        sqs = self.esqs.exclude(content='foo')
        self.assertTrue(isinstance(sqs, EmptySearchQuerySet))
        self.assertEqual(len(sqs), 0)
    
    def test_slice(self):
        sqs = self.esqs.filter(content='foo')
        self.assertTrue(isinstance(sqs, EmptySearchQuerySet))
        self.assertEqual(len(sqs), 0)
        self.assertEqual(sqs[:10], [])
        
        try:
            sqs[4]
            self.fail()
        except IndexError:
            pass
    
    def test_dictionary_lookup(self):
        """
        Ensure doing a dictionary lookup raises a TypeError so
        EmptySearchQuerySets can be used in templates.
        """
        self.assertRaises(TypeError, lambda: self.esqs['count'])


if test_pickling:
    class PickleSearchQuerySetTestCase(TestCase):
        def setUp(self):
            super(PickleSearchQuerySetTestCase, self).setUp()
            # Stow.
            self.old_unified_index = connections['default']._index
            self.ui = UnifiedIndex()
            self.bmmsi = BasicMockModelSearchIndex()
            self.cpkmmsi = CharPKMockModelSearchIndex()
            self.ui.build(indexes=[self.bmmsi, self.cpkmmsi])
            connections['default']._index = self.ui
            
            # Update the "index".
            backend = connections['default'].get_backend()
            backend.clear()
            backend.update(self.bmmsi, MockModel.objects.all())
            
            self.msqs = SearchQuerySet()
            
            # Stow.
            self.old_debug = settings.DEBUG
            settings.DEBUG = True
            
            reset_search_queries()
        
        def tearDown(self):
            # Restore.
            connections['default']._index = self.old_unified_index
            settings.DEBUG = self.old_debug
            super(PickleSearchQuerySetTestCase, self).tearDown()
        
        def test_pickling(self):
            results = self.msqs.all()
            
            for res in results:
                # Make sure the cache is full.
                pass
            
            in_a_pickle = pickle.dumps(results)
            like_a_cuke = pickle.loads(in_a_pickle)
            self.assertEqual(len(like_a_cuke), len(results))
            self.assertEqual(like_a_cuke[0].id, results[0].id)
