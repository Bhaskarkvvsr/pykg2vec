#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This module is for preparing the data
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from pykg2vec.config.global_config import GeneratorConfig
import numpy as np
from multiprocessing import Process, Queue, Value, current_process


def gen_id(ids):
    i = 0
    while True:
        yield ids[i]
        i += 1
        if i >= len(ids):
            np.random.shuffle(ids)
            i = 0


def get_label_mat(data, bs, te, neg_rate=1):
    mat = np.zeros(shape=(bs, te), dtype=np.int8)
    for i in range(bs):
        pos_samples = len(data[i])
        distribution_data = list(data[i])
        for j in range(pos_samples):
            mat[i][distribution_data[j]] = 1
        neg_samples = neg_rate * pos_samples
        idx = list(range(te))
        arr = list(data[i])
        arr.sort(reverse=True)
        for k in arr:
            del idx[k]
        np.random.shuffle(idx)
        for j in range(neg_samples):
            mat[i][idx[j]] = -1
    return mat


def raw_data_generator_trans(raw_queue, processed_queue, data, batch_size, number_of_batch):
    ''' worker process that feeds raw data to raw queues.''' 
    random_ids = np.random.permutation(len(data))
    
    batch_idx = 0
    while True:
        pos_start = batch_size * batch_idx
        pos_end   = batch_size * (batch_idx+1)
        
        raw_data = np.asarray(
            [[data[x].h, data[x].r, data[x].t, data[x].hr_t, data[x].tr_h] for x in random_ids[pos_start:pos_end]]
        )
        raw_queue.put((batch_idx, raw_data))

        batch_idx += 1
        if batch_idx >= number_of_batch:
            batch_idx = 0


def process_function_trans(raw_queue, processed_queue, te, bs, positive_triplets, lh, lr, lt):
    ''' worker process that gets data from raw queue then processes and saves to processed queue.''' 
    negative_triplets = {}

    while True:

        idx, pos_triples = raw_queue.get()
        
        if idx == 0:
            negative_triplets = {}

        ph = pos_triples[:, 0]
        pr = pos_triples[:, 1]
        pt = pos_triples[:, 2]
        
        nh = []
        nr = []
        nt = []

        for t in pos_triples:
            prob = 0.5
            
            if np.random.random() > prob:
                idx_replace_tail = np.random.randint(te)

                break_cnt = 0
                while (t[0], t[1], idx_replace_tail) in positive_triplets or (t[0], t[1], idx_replace_tail) in negative_triplets:
                    idx_replace_tail = np.random.randint(te)
                    break_cnt += 1
                    if break_cnt >= 100:
                        break

                if break_cnt >= 100:  # can not find new negative triple.
                    nh.append(lh)
                    nr.append(lr)
                    nt.append(lt)
                else:
                    nh.append(t[0])
                    nr.append(t[1])
                    nt.append(idx_replace_tail)
                    lh = t[0]
                    lr = t[1]
                    lt = idx_replace_tail

                    negative_triplets[(t[0], t[1], idx_replace_tail)] = 1

            else:
                idx_replace_head = np.random.randint(te)
                break_cnt = 0
                while ((idx_replace_head, t[1], t[2]) in positive_triplets) or (idx_replace_head, t[1], t[2]) in negative_triplets:
                    idx_replace_head = np.random.randint(te)
                    break_cnt += 1
                    if break_cnt >= 100:
                        break

                if break_cnt >= 100:  # can not find new negative triple.
                    nh.append(lh)
                    nr.append(lr)
                    nt.append(lt)
                else:
                    nh.append(idx_replace_head)
                    nr.append(t[1])
                    nt.append(t[2])
                    lh = idx_replace_head
                    lr = t[1]
                    lt = t[2]

                    negative_triplets[(idx_replace_head, t[1], t[2])] = 1

        processed_queue.put([ph, pr, pt, nh, nr, nt])


def process_function(raw_queue, processed_queue, te, bs, neg_rate):
    while True:
        idx, raw_data = raw_queue.get()
        
        h = raw_data[:, 0]
        r = raw_data[:, 1]
        t = raw_data[:, 2]

        hr_t = get_label_mat(raw_data[:, 3], bs, te, neg_rate=neg_rate)
        rt_h = get_label_mat(raw_data[:, 4], bs, te, neg_rate=neg_rate)
        
        processed_queue.put([h, r, t, hr_t, rt_h])


def worker_process_raw_data_testing(raw_queue, processed_queue):
    ''' 
    worker process that gets data from raw queue then processes and saves to processed queue.
    especially for testing data.
    ''' 
    while True:
        pos_triples = raw_queue.get()

        ph = pos_triples[:, 0]
        pr = pos_triples[:, 1]
        pt = pos_triples[:, 2]
        
        processed_queue.put([ph, pr, pt])


class Generator:
    """Generator class for the embedding algorithms
        Args:
          config: generator configuration
        Returns:
          batch for training algorithms
    """

    def __init__(self, config, model_config):

        self.process_list = []
        self.raw_queue = Queue(config.raw_queue_size)
        self.processed_queue = Queue(config.processed_queue_size)

        data = None 
        if   config.data == 'train':
            data = model_config.knowledge_graph.read_cache_data('triplets_train')
        elif config.data == 'test':
            data = model_config.knowledge_graph.read_cache_data('triplets_test')
        elif config.data == 'valid':
            data = model_config.knowledge_graph.read_cache_data('triplets_valid')
        else:
            raise NotImplementedError("The data type passed is wrong!")

        hr_t_ids_train = model_config.knowledge_graph.hr_t_train
        tr_h_ids_train = model_config.knowledge_graph.tr_h_train
        
        # if model_config.sampling == "bern":
        relation_property = model_config.knowledge_graph.relation_property
        
        observed_triples = {(t.h, t.r, t.t): 1 for t in data}
        number_of_batches = len(data) // config.batch_size
         
        self.create_feeder_process(data, config.batch_size, number_of_batches)
        
        if config.data == 'test' or config.data == 'valid':
            self.create_test_processer_process(config.process_num)
        else:
            if config.algo.lower() in ["tucker","tucker_v2","conve", "complex", "distmult", "proje_pointwise"]:
                self.create_train_processor_process(config.process_num, model_config.kg_meta.tot_entity, config.batch_size, config.neg_rate)
            else:
                self.create_train_processor_process_trans(config.process_num, model_config.kg_meta.tot_entity, config.batch_size, observed_triples)

        del model_config, hr_t_ids_train, tr_h_ids_train, relation_property
    
    def __iter__(self):
        return self

    def __next__(self):
        return self.processed_queue.get()
        
    def stop(self):
        for worker_process in self.process_list:
            worker_process.terminate()

    def create_feeder_process(self, data, batch_size, number_of_batch):
        feeder_worker = Process(target=raw_data_generator_trans, \
                                args=(self.raw_queue, self.processed_queue, data, batch_size, number_of_batch))
        feeder_worker.daemon = True
        self.process_list.append(feeder_worker)
        feeder_worker.start()
    
    def create_test_processer_process(self, process_num):
        ''' shared among algorithms '''
        for i in range(process_num):
            process_worker = Process(target=worker_process_raw_data_testing, \
                                     args=(self.raw_queue, self.processed_queue))
            self.process_list.append(process_worker)
            process_worker.daemon = True
            process_worker.start()

    def create_train_processor_process_trans(self, process_num, te, bs, observed_triples):
        ''' special for trans-series algorithms '''
        lh = Value('i', 0)
        lr = Value('i', 0)
        lt = Value('i', 0)
        for i in range(process_num):
            process_worker = Process(target=process_function_trans, \
                                     args=(self.raw_queue, self.processed_queue, te, bs, observed_triples, lh, lr, lt))
            self.process_list.append(process_worker)
            process_worker.daemon = True
            process_worker.start()

    def create_train_processor_process(self, process_num, te, bs, neg_rate):
        for i in range(process_num):
            process_worker = Process(target=process_function, args=(self.raw_queue, self.processed_queue, te, bs, neg_rate))
            self.process_list.append(process_worker)
            process_worker.daemon = True
            process_worker.start()

def test_generator_proje():
    from config.config import ProjE_pointwiseConfig
    config = ProjE_pointwiseConfig()
    config.set_dataset("Freebase15k")
    gen = iter(Generator(config=GeneratorConfig(data='train', algo='ProjE'), model_config=config))
    for i in range(1000):
        data = list(next(gen))
        print("----batch:", i)
        
        hr_hr = data[0]
        hr_t = data[1]
        tr_tr = data[2]
        tr_h = data[3]

        print("hr_hr:", hr_hr)
        print("hr_t:", hr_t)
        print("tr_tr:", tr_tr)
        print("tr_h:", tr_h)
    # gen.stop()


def test_generator_trans():
    
    gen = Generator(config=GeneratorConfig(data='test', algo='TransE'))

    for i in range(1000):
        data = list(next(gen))
        print("----batch:", i)
        ph = data[0]
        pr = data[1]
        pt = data[2]
        # nh = data[3]
        # nr = data[4]
        # nt = data[5]
        print("ph:", ph)
        print("pr:", pr)
        print("pt:", pt)
        # print("nh:", nh)
        # print("nr:", nr)
        # print("nt:", nt)
    gen.stop()


def test_generator():
    import timeit
    start_time = timeit.default_timer()
    from config.config import TransEConfig
    config = TransEConfig()
    config.set_dataset("Freebase15k")

    gen = Generator(config=GeneratorConfig(data='train', algo='tucker'), model_config=config)

    print("----init time:", timeit.default_timer() - start_time)
    for i in range(10):
        start_time_batch = timeit.default_timer()
        data = list(next(gen))
        # import pdb
        # pdb.set_trace()
        h = data[0]
        r = data[1]
        t = data[2]
        # hr_t = data[3]
        # tr_h = data[4]
        print("----batch:", i, "----time:",timeit.default_timer() - start_time_batch)
        # print(h,r,t)# time.sleep(0.05)
        # print("hr_hr:", hr_hr)
        # print("hr_t:", hr_t)
        # print("tr_tr:", tr_tr)
        # print("tr_h:", tr_h)
    print("total time:", timeit.default_timer() - start_time)
    gen.stop()

if __name__ == '__main__':
    # test_generator_proje()
    test_generator()
    # test_generator_conve()
    # test_generator_simple()