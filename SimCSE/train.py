# reference: https://github.com/vdogmcgee/SimCSE-Chinese-Pytorch

import argparse

import numpy as np
import torch
import torch.nn.functional as F
from loguru import logger
from scipy.stats import spearmanr
from torch.utils.data import DataLoader
from tqdm import tqdm
from transformers import BertTokenizer

from dataloader import TrainDataset, TestDataset, load_sts_data, load_sts_data_unsup
from model import SimcseModel, simcse_unsup_loss


def train(model, train_dl, dev_dl, optimizer, device, save_path):
    """模型训练函数"""
    model.train()
    best = 0
    for batch_idx, source in enumerate(tqdm(train_dl), start=1):
        """
        有监督学习：  self.text2id([da[0]]), self.text2id([da[1]]), int(da[2])
        无监督：  self.tokenizer(text, max_length=self.max_len, truncation=True, padding='max_length', return_tensors='pt')
        """
        
        # 维度转换 [batch, 2, seq_len] -> [batch * 2, sql_len]
        real_batch_num = source.get('input_ids').shape[0]
        input_ids = source.get('input_ids').view(real_batch_num * 2, -1).to(device)  # batch_size * 2 , seq_len
        attention_mask = source.get('attention_mask').view(real_batch_num * 2, -1).to(device)
        token_type_ids = source.get('token_type_ids').view(real_batch_num * 2, -1).to(device)
        
        out = model(input_ids, attention_mask, token_type_ids)  # batch_size * 2, 768
        loss = simcse_unsup_loss(out, device)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        
        if batch_idx % 1 == 0:
            logger.info(f'loss: {loss.item():.4f}')
            corrcoef = evaluation(model, dev_dl, device)
            model.train()
            if best < corrcoef:
                best = corrcoef
                # torch.save(model.state_dict(), save_path)
                logger.info(f"higher corrcoef: {best:.4f} in batch: {batch_idx}, save model")


def evaluation(model, dataloader, device):
    """模型评估函数
    批量预测, batch结果拼接, 一次性求spearman相关度
    """
    model.eval()
    sim_tensor = torch.tensor([], device=device)
    label_array = np.array([])
    with torch.no_grad():
        for source, target, label in dataloader:
            # source        [batch, 1, seq_len] -> [batch, seq_len]
            source_input_ids = source.get('input_ids').squeeze(1).to(device)
            source_attention_mask = source.get('attention_mask').squeeze(1).to(device)
            source_token_type_ids = source.get('token_type_ids').squeeze(1).to(device)
            source_pred = model(source_input_ids, source_attention_mask, source_token_type_ids)
            # target        [batch, 1, seq_len] -> [batch, seq_len]
            target_input_ids = target.get('input_ids').squeeze(1).to(device)
            target_attention_mask = target.get('attention_mask').squeeze(1).to(device)
            target_token_type_ids = target.get('token_type_ids').squeeze(1).to(device)
            target_pred = model(target_input_ids, target_attention_mask, target_token_type_ids)
            # concat
            sim = F.cosine_similarity(source_pred, target_pred, dim=-1)
            sim_tensor = torch.cat((sim_tensor, sim), dim=0)
            label_array = np.append(label_array, np.array(label))
    # corrcoef
    return spearmanr(label_array, sim_tensor.cpu().numpy()).correlation


def main(args):
    args.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    
    train_path_sp = args.data_path + "cnsd-sts-train.txt"
    train_path_unsp = args.data_path + "cnsd-sts-train_unsup.txt"
    dev_path_sp = args.data_path + "cnsd-sts-dev.txt"
    test_path_sp = args.data_path + "cnsd-sts-test.txt"
    # pretrain_model_path = "/data/Learn_Project/Backup_Data/macbert_chinese_pretrained"
    
    test_data_source = load_sts_data(test_path_sp)
    tokenizer = BertTokenizer.from_pretrained(args.pretrain_model_path)
    if args.un_supervise:
        train_data_source = load_sts_data_unsup(train_path_unsp)
        train_sents = [data[0] for data in train_data_source]
        train_dataset = TrainDataset(train_sents, tokenizer, max_len=args.max_length)
    else:
        train_data_source = load_sts_data(train_path_sp)
        # train_sents = [data[0] for data in train_data_source] + [data[1] for data in train_data_source]
        train_dataset = TestDataset(train_data_source, tokenizer, max_len=args.max_length)
    
    train_dataloader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=1)
    
    test_dataset = TestDataset(test_data_source, tokenizer, max_len=args.max_length)
    test_dataloader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=True, num_workers=1)
    
    assert args.pooler in ['cls', "pooler", "last-avg", "first-last-avg"]
    model = SimcseModel(pretrained_model=args.pretrain_model_path, pooling=args.pooler, dropout=args.dropout).to(
        args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    
    train(model, train_dataloader, test_dataloader, optimizer, args.device, args.save_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=str, default='cpu', help="gpu or cpu")
    parser.add_argument("--save_path", type=str, default='./model_save')
    parser.add_argument("--un_supervise", type=bool, default=False)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--batch_size", type=float, default=4)
    parser.add_argument("--max_length", type=int, default=16, help="max length of input sentences")
    parser.add_argument("--data_path", type=str, default="../data/STS-B/")
    parser.add_argument("--pretrain_model_path", type=str,
                        default="bert-base-chinese")
    parser.add_argument("--pooler", type=str, choices=['cls', "pooler", "last-avg", "first-last-avg"],
                        default='first-last-avg', help='which pooler to use')
    
    args = parser.parse_args()
    logger.add("../log/train.log")
    logger.info(args)
    main(args)
