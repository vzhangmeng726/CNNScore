import matplotlib
matplotlib.use('Agg')
import sys
import os
import re
import argparse
import numpy as np

import caffe
from caffe.proto import caffe_pb2
from google import protobuf as pb
from sklearn.metrics import roc_curve, auc

import matplotlib.pyplot as plt
import matplotlib.cm as colormap


DEFAULT_BINMAP_ROOT = '/scr/CSAR/'
DEFAULT_OUTPUT_DIR = './'
DEFAULT_GPUS = '0'


def read_lines_from_file(file):
    '''Read lines of a file and split into data fields by whitespace'''
    with open(file, 'r') as f:
        return [line.rstrip().split() for line in f]

def write_lines_to_file(lines, file):
    with open(file, 'w') as f:
        f.write('\n'.join(' '.join(map(str, line)) for line in lines))

def transpose(data):
    '''Transpose the row column majority of data'''
    return [list(i) for i in zip(*data)]

def sort_by_index(data, index, reverse=False):
    '''Sort data lines by a particular field index'''
    return sorted(data, key=lambda row: row[index], reverse=reverse)

def apply_to_fields(data, funcs):
    '''Map a list of functions to the fields in each line of data'''
    return [map(lambda (f,x): f(x), zip(funcs, fields)) for fields in data]

def group_by_index(data, index):
    '''Group data lines into a dict by a field index'''
    d = dict()
    for fields in data:
        group = fields[index]
        if group not in d:
            d[group] = []
        d[group].append(fields[:index]+fields[index+1:])
    return d


def read_data_to_target_dict(data_file, types, index):
    '''Read data lines from file into fields with the provided types,
    then group them into a dictionary by a field index.'''

    data = read_lines_from_file(data_file)
    data = apply_to_fields(data, types)
    data = group_by_index(data, index)
    return data


def read_data_to_field_dict(data_file, types, fields):
    '''Read data lines from file into fields with the provided types,
    then transpose and group the columns into a dictionary using field
    names as the keys.'''

    data = read_lines_from_file(data_file)
    data = apply_to_fields(data, types)
    data = dict(zip(fields, transpose(data)))
    return data


def parse_log_file(log_file, period=2):

    loss_data = dict(iter=[], loss=[])
    i, loss = 0, 0
    for ln, line in enumerate(read_lines_from_file(log_file)):
        if len(line) > 4 and line[4] == 'Iteration':
            i = int(line[5].strip(','))
            loss += float(line[8])
            if (ln%period)+1 == period:
                loss_data['iter'].append(i)
                loss_data['loss'].append(loss/period)
                i, loss = 0, 0

    return loss_data


def plot_training_progress(train_plot_file, loss_data, tot_auroc_data, cv_auroc_data):

    plt.clf()
    plt.figure(figsize=(6, 4))
    plt.title('Training Loss and AUROC')
    plt.xlabel('Iteration')
    plt.ylabel('Percent')

    colors = colormap.rainbow(np.linspace(0, 1, 3))

    plt.plot(loss_data['iter'], loss_data['loss'], '-', color=colors[0], linewidth=2, \
            label='Training Loss')
    plt.plot(tot_auroc_data['iter'], tot_auroc_data['auroc'], '-', color=colors[1], linewidth=2, \
            label='Train set AUROC')
    plt.plot(cv_auroc_data['iter'], cv_auroc_data['auroc'], '-', color=colors[2], linewidth=2, \
            label='Test set AUROC')

    legend = plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.savefig(train_plot_file, bbox_extra_artists=(legend,), bbox_inches='tight')



def reduce_data(data, factor):
    '''Creates a reduced data dict by randomly sampling from each target
    in the given dictionary such that each targets's examples have been
    reduced by the given factor.'''

    reduced = dict()
    for target in data:
        np.random.shuffle(data[target])
        reduced[target] = data[target][:len(data[target])//factor]
    return reduced


def k_fold_partitions(data, k):
    '''Returns a list of k balanced partitions of the data targets,
    where each partition is a list of the targets that should be
    withheld from training and used as the test set.'''

    targets = list(data.keys())
    np.random.shuffle(targets)
    parts = [[] for i in range(k)]
    for i, target in enumerate(targets):
        parts[i%k].append(target)
    return parts


def write_target_data_to_file(data_file, data, targets=None):
    '''Writes data, optionally filtered by a target set, to
    a file with the field order of label, target, example. The
    rows are shuffled.'''

    if targets is None:
        targets = data.keys()

    lines = []
    for target in targets:
        for example in data[target]:
            lines.append(str(example[0]) + ' ' + target + ' ' + example[1])

    np.random.shuffle(lines)

    with open(data_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def write_k_fold_data(full_data_file, k):

    data_dir = os.path.dirname(full_data_file)
    data_param, data_ext = os.path.splitext(os.path.basename(full_data_file))
    full_data = read_data_to_target_dict(full_data_file, [int, str, str], 1)
    parts = k_fold_partitions(full_data, k)
    for p, part in enumerate(parts):

        part_param = 'part' + str(p+1)
        train_data_file = os.path.join(data_dir, '_'.join([data_param, part_param, 'train']) + data_ext)
        test_data_file  = os.path.join(data_dir, '_'.join([data_param, part_param, 'test'])  + data_ext)    
        train_targets = [t for t in full_data if t not in part]
        test_targets = part
        write_target_data_to_file(train_data_file, full_data, train_targets)
        write_target_data_to_file(test_data_file, full_data, test_targets)


def get_vina_roc_data(data_file):

    data = read_data_to_field_dict(data_file, [int, str, str, str, float, float],
        ['labels', 'targets', 'examples', None, None, 'vina'])
    data['vina'] = [-i for i in data['vina']]
    fpr, tpr, thresholds = roc_curve(data['labels'], data['vina'])
    return dict(fpr=fpr, tpr=tpr, auroc=auc(fpr, tpr))


def plot_roc_curves(roc_plot_file, plot_data, vina=None):

    plt.clf()
    plt.figure(figsize=(5, 5))
    plt.title('Receiver Operating Characteristic')
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.plot([0, 1], [0, 1], '--', color=(0.6, 0.6, 0.6), label='random guess')
    
    auroc_data = []
    num_colors = len(plot_data)
    if vina: num_colors += 1
    colors = colormap.rainbow(np.linspace(0, 1, num_colors))

    if vina:
        plt.plot(vina['fpr'], vina['tpr'], '-', color=colors[-1], linewidth=2, \
            label='AutoDock Vina' + ' (AUC=%0.2f)' % vina['auroc'])

    for i, series in enumerate(plot_data):

        fpr, tpr, thresholds = roc_curve(series['labels'], series['scores'])
        auroc = auc(fpr, tpr)
        plt.plot(fpr, tpr, '-', color=colors[i], linewidth=2, \
            label=series.get('name', 'series %d' % i) + ' (AUC=%0.2f)' % auroc)
        auroc_data.append(auroc)

    legend = plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.savefig(roc_plot_file, bbox_extra_artists=(legend,), bbox_inches='tight')

    return auroc_data


def write_scores_to_file(score_file, results):

    buf = ''
    for i, example in enumerate(results['examples']):
        target = results['targets'][i]
        label = results['labels'][i]
        score = results['scores'][i]
        buf += ' '.join(map(str, [label, target, example, score])) + '\n'

    with open(score_file, 'w') as f:
        f.write(buf)


def get_model_predictions(model_file, weight_file, data_file):

    data = read_data_to_field_dict(data_file, types=[int, str, str], \
        fields=['labels', 'targets', 'examples'])
    data['scores'] = []
    
    model = caffe.Net(model_file, weight_file, caffe.TEST)
    batch_size = model.blobs['data'].shape[0]
    num_examples = len(data['examples'])
    num_batches = num_examples//batch_size + 1

    c = 0
    for i in range(num_batches):

        # this assumes that model scores examples in same
        # order in which they appear in the data file
        print('%s | %s: batch %d / %d' % (weight_file, data_file, i, num_batches))

        output = model.forward()
        for j in range(batch_size):
        
            data_index = i*batch_size + j
            if data_index >= num_examples:
                break

            if data['labels'][data_index] != int(output['label'][j]):
                raise IndexError('file data does not match model data')
            data['scores'].append(output['pred'][j][1])

    return data


def write_model_prototxt(model_file, model_prototype, data_file, binmap_root, mode):
    
    model = caffe_pb2.NetParameter()
    model.CopyFrom(model_prototype)

    data_layer = model.layer[0]
    data_layer.ndim_data_param.source = data_file
    data_layer.ndim_data_param.root_folder = binmap_root

    if mode == 'train':
        for i, layer in enumerate(model.layer):
            if layer.name == 'pred':
                del model.layer[i]
    
    elif mode == 'test':
        data_layer.ndim_data_param.shuffle = False
        data_layer.ndim_data_param.balanced = False
        for i, layer in enumerate(model.layer):
            if layer.name == 'loss':
                del model.layer[i]

    with open(model_file, 'w') as f:
        f.write(str(model))


def write_solver_prototxt(solver_file, solver_prototype, model_file):

    solver = caffe_pb2.SolverParameter()
    solver.CopyFrom(solver_prototype)

    solver.train_net = model_file
    weight_prefix = model_file.replace('_train.model.prototxt', '')
    solver.snapshot_prefix = weight_prefix

    with open(solver_file, 'w') as f:
        f.write(str(solver))


def read_model_prototxt(model_file):

    model = caffe_pb2.NetParameter()
    with open(model_file, 'r') as f:
        pb.text_format.Merge(str(f.read()), model)
    return model


def read_solver_prototxt(solver_file):

    solver = caffe_pb2.SolverParameter()
    with open(solver_file, 'r') as f:
        pb.text_format.Merge(str(f.read()), solver)
    return solver


def generate_crossval_files(output_dir, full_data_file, binmap_root, model_prototype_file, solver_prototype_file, k=3, write=False):

    # keep track of names of all generated files in a dictionary
    crossval_files = dict(train_data=[], test_data=[], train_models=[], test_models=[], \
        solvers=[], weights=[], scores=[], roc_plots=dict(crossval=None, testontrain=None, by_iter=[]), line_plot=None)

    # get parameter strings for data, model, and solver arguments
    data_dir = os.path.dirname(full_data_file)
    data_param, data_ext = os.path.splitext(os.path.basename(full_data_file))
    model_param  = os.path.basename(model_prototype_file).split('.')[0] # model_param.model.prototxt
    solver_param = os.path.basename(solver_prototype_file).split('.')[0] # solver_param.solver.prototxt

    model_prototype  = read_model_prototxt(model_prototype_file)
    solver_prototype = read_solver_prototxt(solver_prototype_file)
    max_iter = solver_prototype.max_iter
    snap_iter = solver_prototype.snapshot
    if not snap_iter:
        snap_iter = max_iter

    for i in range(k+1):

        if i == 0:
            part_param = 'full'
            train_data_file = full_data_file
            test_data_file  = full_data_file
        else:
            part_param = 'part' + str(i)
            train_data_file = os.path.join(data_dir, '_'.join([data_param, part_param, 'train']) + data_ext)
            test_data_file  = os.path.join(data_dir, '_'.join([data_param, part_param, 'test'])  + data_ext)

        crossval_files['train_data'].append(train_data_file)
        crossval_files['test_data'].append(test_data_file)


        # prototxt for train model, test model and solver   
        train_model_file = os.path.join(output_dir, '_'.join([model_param, part_param, 'train']) + '.model.prototxt')
        test_model_file  = os.path.join(output_dir, '_'.join([model_param, part_param, 'test'])  + '.model.prototxt')
        solver_file = os.path.join(output_dir, '_'.join([solver_param, part_param]) + '.solver.prototxt')
        
        if write:
            write_model_prototxt(train_model_file, model_prototype, train_data_file, binmap_root, mode='train')
            write_model_prototxt(test_model_file,  model_prototype, test_data_file,  binmap_root, mode='test')
            write_solver_prototxt(solver_file, solver_prototype, train_model_file)
        
        crossval_files['train_models'].append(train_model_file)
        crossval_files['test_models'].append(test_model_file)
        crossval_files['solvers'].append(solver_file)


        # keep track of weight files and score files that will be produced from training snapshots
        weight_files, score_files = [], []
        for j in range(snap_iter, max_iter+1, snap_iter):
            iter_param = 'iter_' + str(j)
            weight_file = os.path.join(output_dir, '_'.join([model_param, part_param, iter_param]) + '.caffemodel')
            score_file  = os.path.join(output_dir, '_'.join([model_param, part_param, iter_param]) + '.scores')
            weight_files.append(weight_file)
            score_files.append(score_file)

        crossval_files['weights'].append(weight_files)
        crossval_files['scores'].append(score_files)


    # plot ROC curves for test-on-train, crossval, and both by training iteration
    for j in range(snap_iter, max_iter+1, snap_iter):
        iter_param = 'iter_' + str(j)
        roc_plot_file = os.path.join(output_dir, '_'.join([model_param, iter_param]) + '.roc.png')
        crossval_files['roc_plots']['by_iter'].append(roc_plot_file)

    roc_plot_file = os.path.join(output_dir, '_'.join([model_param, "testontrain"]) + '.roc.png')
    crossval_files['roc_plots']['testontrain']= roc_plot_file

    roc_plot_file = os.path.join(output_dir, '_'.join([model_param, "crossval"]) + '.roc.png')
    crossval_files['roc_plots']['crossval'] = roc_plot_file

    line_plot_file = os.path.join(output_dir, '_'.join([model_param]) + '.line.png')
    crossval_files['line_plot'] = line_plot_file

    return crossval_files


def generate_test_files(output_dir, test_data_file, binmap_root, model_prototype_file, weight_file, write=False):

    test_files = dict(test_data=None, test_model=None, score=None)

    # get parameter strings for data, model, and solver arguments
    data_param, data_ext = os.path.splitext(os.path.basename(test_data_file)) # data_param.data_ext
    model_param = os.path.basename(model_prototype_file).split('.')[0] # model_param.model.prototxt

    test_model_file = os.path.join(output_dir, '_'.join([model_param, data_param])  + '.model.prototxt')
    
    if write:
        model_prototype = read_model_prototxt(model_prototype_file)
        write_model_prototxt(test_model_file, model_prototype, test_data_file, binmap_root, mode='test')

    weight_param = os.path.splitext(os.path.basename(weight_file))[0]
    score_file = os.path.join(output_dir, '_'.join([weight_param, data_param]) + '.scores')

    test_files['test_data'] = test_data_file
    test_files['test_model'] = test_model_file
    test_files['score'] = score_file

    return test_files


def draw_crossval_plots(output_dir, data_file, model_file, solver_file, opts, log_file=None):

    k = 3
    binmap_root = opts.get('b', DEFAULT_BINMAP_ROOT)
    crossval_files = generate_crossval_files(output_dir, data_file, binmap_root, model_file, solver_file, k, write=False)
    
    auroc_iters = []
    all_score_data = [[], []] # testontrain, crossval
    for i in range(k+1):

        for j, weight_file in enumerate(crossval_files['weights'][i]):
            score_file = crossval_files['scores'][i][j]
            score_data = read_data_to_field_dict(score_file, types=[int, str, str, float], \
                                    fields=['labels', 'targets', 'examples', 'scores'])
            if i == 0:
                score_data['name'] = "ConvNet, test-on-train"
                all_score_data[0].append(score_data)
                auroc_iters.append(int(re.search(r'iter_(\d+)', score_file).group(1)))
            elif i == 1:
                score_data['name'] = "ConvNet, cross-validation"
                all_score_data[1].append(score_data)
            else:
                all_score_data[1][j]['labels'] += score_data['labels']
                all_score_data[1][j]['targets'] += score_data['targets']
                all_score_data[1][j]['examples'] += score_data['examples']
                all_score_data[1][j]['scores'] += score_data['scores']

    tot_aurocs = plot_roc_curves(crossval_files['roc_plots']['testontrain'], all_score_data[0])
    cv_aurocs = plot_roc_curves(crossval_files['roc_plots']['crossval'], all_score_data[1])
    if log_file:
        loss_data = parse_log_file(log_file)
        tot_auroc_data = dict(iter=[0]+auroc_iters, auroc=[0.5]+tot_aurocs)
        cv_auroc_data = dict(iter=[0]+auroc_iters, auroc=[0.5]+cv_aurocs)
        train_plot_file = crossval_files['roc_plots']['crossval'].replace('_crossval.roc.png', '.train_progress.png')
        plot_training_progress(train_plot_file, loss_data, tot_auroc_data, cv_auroc_data)

    vina_roc_data = get_vina_roc_data(data_file)
    by_iter_score_data = transpose(all_score_data)
    for i, roc_plot_file in enumerate(crossval_files['roc_plots']['by_iter']):
        plot_roc_curves(roc_plot_file, by_iter_score_data[i], vina=vina_roc_data)



def crossval_model(output_dir, data_file, model_file, solver_file, opts):

    k = 3
    binmap_root = opts.b or DEFAULT_BINMAP_ROOT
    crossval_files = generate_crossval_files(output_dir, data_file, binmap_root, model_file, solver_file, k, write=True)
    
    gpus = opts.g or DEFAULT_GPUS
    for i in range(k+1):
        solver_file = crossval_files['solvers'][i]
        os.system('caffe train -solver ' + solver_file + ' -gpu ' + gpus)

    caffe.set_device(int(gpus.split(',')[0])) # can pycaffe do multi-gpu?
    caffe.set_mode_gpu()
    
    all_score_data = [[], []] # testontrain, crossval
    for i in range(k+1):
        test_data_file = crossval_files['test_data'][i]
        test_model_file = crossval_files['test_models'][i]

        for j, weight_file in enumerate(crossval_files['weights'][i]):
            score_file = crossval_files['scores'][i][j]
            score_data = get_model_predictions(test_model_file, weight_file, test_data_file)
            write_scores_to_file(score_file, score_data)

            if i == 0:
                score_data['name'] = os.path.basename(score_file)[:-7].replace("full", "testontrain")
                all_score_data[0].append(score_data)
            elif i == 1:
                score_data['name'] = os.path.basename(score_file)[:-7].replace("part1", "crossval")
                all_score_data[1].append(score_data)
            else:
                all_score_data[1][j]['labels'] += score_data['labels']
                all_score_data[1][j]['targets'] += score_data['targets']
                all_score_data[1][j]['examples'] += score_data['examples']
                all_score_data[1][j]['scores'] += score_data['scores']

    plot_roc_curves(crossval_files['roc_plots']['testontrain'], all_score_data[0])
    plot_roc_curves(crossval_files['roc_plots']['crossval'], all_score_data[1])

    by_iter_score_data = transpose(all_score_data)
    for i, roc_plot_file in enumerate(crossval_files['roc_plots']['by_iter']):
        plot_roc_curves(roc_plot_file, by_iter_score_data[i])


def test_model(output_dir, data_file, model_file, weight_file, opts):

    binmap_root = opts.b or DEFAULT_BINMAP_ROOT
    test_files = generate_test_files(output_dir, data_file, binmap_root, model_file, weight_file, write=True)
    
    gpus = opts.g or DEFAULT_GPUS
    caffe.set_device(int(gpus.split(',')[0])) 
    caffe.set_mode_gpu()
    test_data_file = test_files['test_data']
    test_model_file = test_files['test_model']
    results = get_model_predictions(test_model_file, weight_file, test_data_file)

    score_file = test_files['score']
    write_scores_to_file(score_file, results)


def parse_args(argv):

    p_global = argparse.ArgumentParser(add_help=False)
    p_global.add_argument('-g', metavar='<gpus>', \
        help='comma-separated device ids of GPUs to use')
    p_global.add_argument('-o', metavar='<output_dir>', \
        help='directory to output generated files')
    p_global.add_argument('-b', metavar='<binmap_root>', \
        help='root of binmap directory tree')

    p_task = argparse.ArgumentParser(parents=[p_global])
    subparsers = p_task.add_subparsers(title='task', metavar='<crossval|test>', \
        help='perform cross-validation training or testing')
    subparsers.required = True

    p_crossval = subparsers.add_parser('crossval', parents=[p_global])
    p_crossval.set_defaults(task='crossval')
    p_crossval.add_argument('data_file', metavar='<data_file>', \
        help='dataset for training and validation')
    p_crossval.add_argument('model_file', metavar='<model_file>', \
        help='model definition prototxt')
    p_crossval.add_argument('solver_file', metavar='<solver_file>', \
        help='solver definition prototxt')

    p_test = subparsers.add_parser('test', parents=[p_global])
    p_test.set_defaults(task='test')
    p_test.add_argument('data_file', metavar='<data_file>', \
        help='dataset to produce scores for')
    p_test.add_argument('model_file', metavar='<model_file>', \
        help='model definition prototxt')
    p_test.add_argument('weight_file', metavar='<weight_file>', \
        help='caffemodel to use as model weights')

    return p_task.parse_args(argv[1:])


def main(argv=sys.argv):

    args = parse_args(argv)
    output_dir = args.o or DEFAULT_OUTPUT_DIR
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    if args.task == 'crossval':
        crossval_model(output_dir, args.data_file, args.model_file, args.solver_file, args)
    elif args.task == 'test':
        test_model(output_dir, args.data_file, args.model_file, args.weight_file, args)


if __name__ == '__main__':
    sys.exit(main())
