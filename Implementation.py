# -*- coding: utf-8 -*-
"""570.ipynb

Automatically generated by Colaboratory.

"""

# Mount Google Drive
from google.colab import drive
drive.mount('/content/drive')

# Imports
import torch
from torch.utils.data import Dataset
from torch import nn
from torch.autograd import Variable
from torch.utils.data import DataLoader
import torch.nn.functional as F
import json
from torch.optim import Adam
import numpy as np

# Path and hyperparameters set up
IMG_PATH = '/content/drive/My Drive/data/res18.pkl'
VOCAB_PATH = '/content/drive/My Drive/data/dicts-30000.json'
TRAIN_PATH = '/content/drive/My Drive/data/train-context.json'
TEST_PATH = '/content/drive/My Drive/data/test-candidate.json'
N_CON = 5
N_FRAME = 5
MAX_LEN = 20
BATCH_SIZE = 128
VEC_SIZE = 512
N_HIDDEN = 512
N_EPOCH = 50

# Use GPU for training
USE_CUDA = True
device = torch.device("cuda")


def load_from_json(path):
    """
    Load training/testing data from json file, adapted from livebot github
    :param path: path to the json file
    :return: data: list of dictionary
    """
    datas = []
    with open(path, 'r', encoding='utf8') as myfile:
        for line in myfile:
            data = json.loads(line)
            datas.append(data)
    return datas


class CustomDataset(Dataset):
    print('Initializing...')
    # Images and vocabulary information loaded as class level variables
    imgs = torch.load(open(IMG_PATH, 'rb'))
    vocab = json.load(open(VOCAB_PATH, 'r', encoding='utf8'))['word2id']
    rev_vocab = json.load(open(VOCAB_PATH, 'r', encoding='utf8'))['id2word']
    vocab_size = len(vocab)

    def __init__(self, path, n_con=5, n_frame=5, maxlen=MAX_LEN,
                 is_train=True, is_test=False):
        """
        Dataset initialization
        :param path: path to the data json file
        :param n_con: number of comments to use as context
        :param n_frame: number of frames to use as video context
        :param maxlen: maximum length of comment
        :param is_train: training flag
        :param is_test: testing flag
        """
        print('Loading data from {}...'.format(path))
        data = load_from_json(path)
        print('...Done loading')

        # Separate data into components
        self.length = len(data)
        self.videoid = [data[i]['video'] for i in range(self.length)]
        self.time = [data[i]['time'] for i in range(self.length)]
        self.context = [data[i]['context'] for i in range(self.length)]
        self.comment = [data[i]['comment'] for i in range(self.length)]
        print('Number of samples: {}'.format(self.length))

        # Parameters set up
        self.n_con = n_con
        self.n_frame = n_frame
        self.max_len = maxlen
        self.is_train = is_train
        self.is_test = is_test

        # Candidate component only for evaluating
        if is_train or is_test:
            self.candidate = []
        else:
            self.candidate = [data[i]['candidate'] for i in range(self.length)]

    def __len__(self):
        """
        Get length of dataset
        :return: length
        """
        return self.length

    def __getitem__(self, ind):
        """
        Get one sample from the dataset
        :param ind: index of sample
        :return: X: video context, Y: ground truth comment, T: comment context
        """
        vid = self.videoid[ind]
        time = self.time[ind] - 1
        context = self.context[ind]
        comment = self.comment[ind]
        # Load video frames
        X = CustomDataset.load_img(vid, time, self.n_frame)
        # Load ground truth
        if self.is_train:
            Y = CustomDataset.padding(comment, self.max_len)
        elif self.is_test:
            comment = comment[0]
            Y = CustomDataset.padding(comment, self.max_len)
        else:
            Y = self.candidate[ind]
        # Load comment context
        T = CustomDataset.padding(context, self.max_len * self.n_con)
        return X, Y, T

    @staticmethod
    def load_img(self, vid, time, n_frame):
        """
        Load video frames at specific id and time
        :param self:
        :param vid:
        :param time:
        :param n_frame:
        :return:
        """
        surroundings = [0, -1, 1, -2, 2, -3, 3, -4, 4]
        X = []
        ct = 0
        for t in surroundings:
            if 0 <= time + t < len(CustomDataset.imgs[vid]):
                X.append(CustomDataset.imgs[vid][time + t])
                ct += 1
            if ct == n_frame:
                break
        return torch.stack(X)

    @staticmethod
    def padding(x, max_len):
        """
        Pad comments to the same length (max_len) for training with batch
        :param x: comment to be padded
        :param max_len: maximum length to pad to
        :return: padded comment
        """
        x = x.split()
        if len(x) > max_len - 2:
            x = x[:max_len - 2]
        Y = list(map(lambda t: CustomDataset.vocab.get(t, 3), x))
        Y = [1] + Y + [2]
        length = len(Y)
        Y = torch.cat([torch.LongTensor(Y), 
                       torch.zeros(max_len - length).long()])
        return Y


class Attn(nn.Module):
    def __init__(self, method, hidden_size):
        """
        Initializing the attention module
        :param method: method for attention ('dot', 'general', 'concat')
        :param hidden_size: size for hidden size
        """
        super(Attn, self).__init__()

        self.method = method
        self.hidden_size = hidden_size

        if self.method == 'general':
            self.attn = nn.Linear(self.hidden_size, hidden_size)

        elif self.method == 'concat':
            self.attn = nn.Linear(self.hidden_size * 2, hidden_size)
            self.v = nn.Parameter(torch.FloatTensor(1, hidden_size))

    def forward(self, hidden, encoder_outputs):
        """
        Foward attention computation
        :param hidden: hidden
        :param encoder_outputs: outputs from encoders
        :return: attention out
        """
        max_len = encoder_outputs.size(1)
        this_batch_size = encoder_outputs.size(0)

        attn_energies = Variable(torch.zeros(this_batch_size, max_len))  # B x S

        for b in range(this_batch_size):
            for i in range(max_len):
                attn_energies[b, i] = self.score(hidden[:, b], encoder_outputs[b, i].unsqueeze(0))

        return F.softmax(attn_energies).unsqueeze(1)

    def score(self, hidden, encoder_output):
        """
        Calculate energy based on selected method
        :param hidden: hidden
        :param encoder_output: outputs from encoders
        :return: attention energy
        """
        if self.method == 'dot':
            energy = hidden.dot(encoder_output)
            return energy

        elif self.method == 'general':
            energy = self.attn(encoder_output)
            energy = hidden.squeeze(0).dot(energy.squeeze(0))
            return energy

        elif self.method == 'concat':
            energy = self.attn(torch.cat((hidden, encoder_output), 1))
            energy = self.v.dot(energy)
            return energy


class VideoEncoder(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(VideoEncoder, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.gru = nn.GRU(input_size=input_size, hidden_size=hidden_size, 
                          num_layers=1, batch_first=True)

    def forward(self, X, h=None):
        outputs, hidden = self.gru(X, h)
        return outputs, hidden


class TextEncoder(nn.Module):
    def __init__(self, vocab_size, hidden_size):
        super(TextEncoder, self).__init__()
        self.input_size = vocab_size
        self.hidden_size = hidden_size
        self.embedding = nn.Embedding(vocab_size, hidden_size).cuda()
        self.gru = nn.GRU(input_size=hidden_size, hidden_size=hidden_size, 
                          num_layers=1, batch_first=True)

    def forward(self, T, h=None):
        embedded = self.embedding(T.long().to(device))
        outputs, hidden = self.gru(embedded, h)
        return outputs, hidden

    def init_hidden(self, batch_size):
        return torch.zeros(2, batch_size, self.hidden_size, device=device)


class CommentDecoder(nn.Module):
    def __init__(self, attn_model, hidden_size, output_size):
        super(CommentDecoder, self).__init__()
        self.attn_model = attn_model
        self.hidden_size = hidden_size
        self.output_size = output_size

        self.embedding = nn.Embedding(output_size, hidden_size).cuda()
        self.gru = nn.GRU(hidden_size, hidden_size).cuda()
        self.concat = nn.Linear(hidden_size * 2, hidden_size).cuda()
        self.out = nn.Linear(hidden_size, output_size).cuda()

        if attn_model != 'none':
            self.attn = Attn(attn_model, hidden_size).cuda()

    def forward(self, input_seq, last_hidden, t_h, v_h):
        batch_size = input_seq.size(0)
        embedded = self.embedding(input_seq.to(device))
        embedded = embedded.view(1, batch_size, self.hidden_size)

        rnn_output, hidden = self.gru(embedded, last_hidden)

        attn_tv = self.attn(t_h, v_h)
        context = attn_tv.bmm(rnn_output.to(torch.device('cpu')))
        context = context.cuda()

        rnn_output = rnn_output.squeeze(0)
        context = context.squeeze(1)
        concat_input = torch.cat((rnn_output, context), 1)
        concat_output = torch.tanh(self.concat(concat_input))

        output = self.out(concat_output)

        return output, hidden, attn_tv


class Model(nn.Module):
    def __init__(self, v_encoder, t_encoder, decoder, train=True):
        """
        Initialize model
        :param v_encoder: video encoder
        :param t_encoder: text encoder
        :param decoder: comment decoder
        :param train: train flag
        """
        super(Model, self).__init__()
        self.v_encoder = v_encoder
        self.t_encoder = t_encoder
        self.decoder = decoder
        self.max_len = MAX_LEN
        self.criterion = nn.CrossEntropyLoss()
        self.train = train

    def forward(self, X, Y, T):
        batch_size = X.size(0)
        t_out, t_h = self.t_encoder(T)
        v_out, v_h = self.v_encoder(X)
        decoder_input = Variable(torch.LongTensor([1] * batch_size))
        decoder_hidden = None

        loss = 0
        all_decoder_outputs = \
        Variable(torch.zeros(self.max_len, batch_size, self.decoder.output_size))
        for t in range(self.max_len - 1):
            decoder_output, decoder_hidden, decoder_attn = self.decoder(
                decoder_input, decoder_hidden, t_h, v_h
            )
            all_decoder_outputs[t] = decoder_output
            loss += self.criterion(decoder_output, Y[:, t+1])
            
            if self.train:
                decoder_input = Y[:, t+1]
            else:
                val, decoder_input = torch.max(decoder_output, 1)
            
        loss /= get_y_len(list(Y.squeeze()))

        output = all_decoder_outputs.transpose(0,1).contiguous()\
        .view(-1, CustomDataset.vocab_size)

        return output, loss


def printOut(out):
    """
    Print the output
    :param out: output, list of size MAX_LEN
    :return: None
    """
    for i in range(MAX_LEN):
        c = CustomDataset.rev_vocab[str(int(out[i]))]
        if c == '<EOS>':
            break
        if c not in ['<EOS>', '<BOS>', '<UNK>', '<&&&>', '<PAD>']:
            print(c, end='')
    print()


def train(resume=False):
    """
    Training
    :param resume: flag for loading checkpoint
    :return: None
    """
    # Initialize model
    dataset = CustomDataset(TRAIN_PATH)
    sub = list(range(1, len(dataset), 80))
    dataset = torch.utils.data.Subset(dataset, sub)
    loader = DataLoader(dataset=dataset, batch_size=BATCH_SIZE, shuffle=True)
    t_encoder = TextEncoder(CustomDataset.vocab_size, N_HIDDEN).cuda()
    v_encoder = VideoEncoder(VEC_SIZE, N_HIDDEN).cuda()
    decoder = CommentDecoder('general', N_HIDDEN, CustomDataset.vocab_size).cuda()

    # Load checkpoints
    if resume:
        print("Loading ckpt...")
        t_encoder.load_state_dict(torch.load(
            '/content/drive/My Drive/ckpt/t_encoder_new.pt'))
        v_encoder.load_state_dict(torch.load(
            '/content/drive/My Drive/ckpt/v_encoder_new.pt'))
        decoder.load_state_dict(torch.load(
            '/content/drive/My Drive/ckpt/decoder_new.pt'))
        print("...Done loading")

    # Optimizer set up
    t_optim = Adam(t_encoder.parameters())
    v_optim = Adam(v_encoder.parameters())
    d_optim = Adam(decoder.parameters())
    model = Model(v_encoder, t_encoder, decoder).cuda()

    # Train for n epoch
    for i in range(N_EPOCH):
        total_loss = 0
        count = 0
        batch_loss = 0
        for batch in loader:
            count += 1
            X, Y, T = batch
            X = X.to(device)
            Y = Y.to(device)
            T = T.to(device)

            # Clear gradients
            t_optim.zero_grad()
            v_optim.zero_grad()
            d_optim.zero_grad()

            output, loss = model(X, Y, T)
            loss.backward()
            total_loss += loss.item()

            t_optim.step()
            v_optim.step()
            d_optim.step()

            with open('/content/drive/My Drive/ckpt/logfile.txt', 'a') as myfile:
                myfile.write("Epoch {} Batch {}: Loss {}\n".format(i+1, count, batch_loss))

        with open('/content/drive/My Drive/ckpt/logfile.txt', 'a') as myfile:
            myfile.write("Epoch {}: Average Loss = {}\n".format(i+1, total_loss / count))

        # save model after each epoch
        torch.save(t_encoder.state_dict(), 
                   '/content/drive/My Drive/ckpt/t_encoder_new.pt')
        torch.save(v_encoder.state_dict(), 
                   '/content/drive/My Drive/ckpt/v_encoder_new.pt')
        torch.save(decoder.state_dict(), 
                   '/content/drive/My Drive/ckpt/decoder_new.pt')


def test():
    """
    Print output and ground truth comparison
    :return: None
    """
    dataset = CustomDataset(path=TEST_PATH, is_train=False, is_test=True)
    loader = DataLoader(dataset=dataset, batch_size=1)
    t_encoder = TextEncoder(CustomDataset.vocab_size, N_HIDDEN).cuda()
    v_encoder = VideoEncoder(VEC_SIZE, N_HIDDEN).cuda()
    decoder = CommentDecoder('general', N_HIDDEN, CustomDataset.vocab_size).cuda()
    t_encoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/t_encoder_new.pt'))
    v_encoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/v_encoder_new.pt'))
    decoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/decoder_new.pt'))
    model = Model(v_encoder, t_encoder, decoder, train=False).cuda()
    with torch.no_grad():
        for batch in loader:
            X, Y, T = batch
            X = X.to(device)
            Y = Y.to(device)
            T = T.to(device)
            output, loss = model(X, Y, T)
            _vals, output = output.topk(1)
            print("Expected:")
            printOut(Y.squeeze())
            print("Model output:")
            printOut(output.squeeze())


def calc_hit_rank(prediction, reference):
    """
    Calculate the ranking of human-evaluated comment
    :param prediction: model ranking + comments
    :param reference: dictionary of comment-to-category (1-5)
    :return: rank
    """
    for i, p in enumerate(prediction):
        if reference[p] == 1:
            return i+1


def recall(predictions, references, k=1):
    """
    Calculate the percentage of the hit rank within range k
    :param predictions: model ranking + comments
    :param references: dictionary of comment-to-category (1-5)
    :param k: range to accept rank
    :return: percentage
    """
    total = len(references)
    hits = 0
    for p, c in zip(predictions, references):
        hits += int(calc_hit_rank(p, c) <= k)
    return hits * 100.0 / total


def mean_rank(predictions, references):
    """
    Calculate the mean ranking of category 1 comment
    :param predictions: model ranking + comments
    :param references: dictionary of comment-to-category (1-5)
    :return: mean rank
    """
    ranks = []
    for p, c in zip(predictions, references):
        rank = calc_hit_rank(p, c)
        ranks.append(rank)
    return sum(ranks) * 1.0 / len(ranks)


def mean_reciprocal_rank(predictions, references):
    """
    Calculate the mean reciprocal ranking of category 1 comment
    :param predictions: model ranking + comments
    :param references: dictionary of comment-to-category (1-5)
    :return: mean reciprocal rank
    """
    ranks = []
    for p, c in zip(predictions, references):
        rank = calc_hit_rank(p, c)
        ranks.append(1.0 / rank)
    return sum(ranks) * 1.0 / len(ranks)


def get_y_len(Y):
    """
    Get length of comment
    :param Y: comment
    :return: length
    """
    for i, x in enumerate(Y):
        if x == 0:
            return max(i - 1, 1)
    return MAX_LEN - 2


def evaluate():
    """
    Evaluate the model by ranking
    :return: None
    """
    dataset = CustomDataset(path=TEST_PATH, is_train=False, is_test=False)
    sub = list(range(1, len(dataset), 80))
    dataset = torch.utils.data.Subset(dataset, sub)
    loader = DataLoader(dataset=dataset, batch_size=1)
    t_encoder = TextEncoder(CustomDataset.vocab_size, N_HIDDEN).cuda()
    v_encoder = VideoEncoder(VEC_SIZE, N_HIDDEN).cuda()
    decoder = CommentDecoder('general', N_HIDDEN, CustomDataset.vocab_size).cuda()
    t_encoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/t_encoder_new.pt'))
    v_encoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/v_encoder_new.pt'))
    decoder.load_state_dict(torch.load(
        '/content/drive/My Drive/ckpt/decoder_new.pt'))
    model = Model(v_encoder, t_encoder, decoder, train=False).cuda()

    predictions = []
    references = []
    with torch.no_grad():
        for sample in loader:
            X, candidate, T = sample
            comments = list(candidate.keys())
            X = Variable(X).cuda()
            Y = [CustomDataset.padding(c, MAX_LEN) for c in candidate]
            Y = torch.stack(Y).unsqueeze(0)
            Y = Variable(Y).cuda()
            T = Variable(T).cuda()

            loss = []
            tmp = []

            for i in range(Y.size(1)):
                output, l = model(X, Y[:,i,:], T)
                loss.append(l.item())

            loss = np.array(loss)
            rank = list(np.argsort(loss))
            for r in rank:
                tmp.append(comments[r])
            predictions.append(tmp)
            references.append(candidate)
    recall_1 = recall(predictions, references, 1)
    recall_5 = recall(predictions, references, 5)
    recall_10 = recall(predictions, references, 10)
    mr = mean_rank(predictions, references)
    mrr = mean_reciprocal_rank(predictions, references)
    print(recall_1, recall_5, recall_10, mr, mrr)


if __name__ == "__main__":
    train(True)
    test()
    evaluate()

